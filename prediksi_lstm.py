"""
Prediksi Volume Sampah — LSTM Attention (TFLite)
Port dari web_inference/app.py, feature engineering & scaler PERSIS SAMA,
tapi inference pakai tflite-runtime (ringan, ~tanpa TensorFlow penuh).

Dipakai oleh main.py (FastAPI EcoSort, port 8000).
File yang dibutuhkan di folder backend:
    - volume_attention_lstm.tflite   (hasil convert_lstm_tflite.py)
    - scaler_mean.npy, scaler_scale.npy
    - data/sampahbandung_normal_monthly.csv
"""

import os
import csv
import math
from datetime import datetime

import numpy as np

# tflite-runtime (di Pi) atau ai_edge_litert / tensorflow (desktop)
try:
    import tflite_runtime.interpreter as tflite
except ImportError:
    try:
        import ai_edge_litert.interpreter as tflite
    except ImportError:
        import tensorflow.lite as tflite  # fallback desktop

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
TFLITE_PATH = os.path.join(BASE_DIR, "volume_attention_lstm.tflite")
SCALER_MEAN = os.path.join(BASE_DIR, "scaler_mean.npy")
SCALER_SCALE= os.path.join(BASE_DIR, "scaler_scale.npy")
CSV_PATH    = os.path.join(BASE_DIR, "data", "sampahbandung_normal_monthly.csv")

# ─── Konstanta PERSIS app.py ──────────────────────────────────────────────────
SEASONAL_FACTOR = {1:1.20,2:1.05,3:1.15,4:1.10,5:1.00,6:0.95,
                   7:0.88,8:0.88,9:0.92,10:0.98,11:1.05,12:1.25}
VOL_BASE   = {"URBAN":15.0, "SEMI_URBAN":6.5, "RURAL":2.0}
AREA_ENC   = {"RURAL":0, "SEMI_URBAN":1, "URBAN":2}
AREA_LABEL = {"URBAN":"Perkotaan", "SEMI_URBAN":"Pinggiran Kota", "RURAL":"Pedesaan"}
AREA_TYPE_FROM_CSV = {"metropolitan":"URBAN", "semi urban":"SEMI_URBAN", "pedesaan":"RURAL"}

WINDOW_SIZE = 12
HORIZON     = 3
YR_MIN, YR_MAX = 2017, 2026
LBL = ["Jan","Feb","Mar","Apr","Mei","Jun","Jul","Agu","Sep","Okt","Nov","Des"]
FEATURE_COLS = ['volume_ton','month_sin','month_cos','area_enc','year_norm','vol_ma3']

# ─── State global ─────────────────────────────────────────────────────────────
_interp = None
_inp = None
_out = None
_scaler_mean = None
_scaler_scale = None
DF_VOL_AGG: dict = {}   # area_type → list[dict] terurut tanggal
KEC_AREA: dict = {}     # kecamatan UPPER → area_type app


def _load_model():
    global _interp, _inp, _out, _scaler_mean, _scaler_scale
    if not os.path.exists(TFLITE_PATH):
        print(f"[LSTM-TFLite] Model tidak ada: {TFLITE_PATH}")
        return False
    try:
        _interp = tflite.Interpreter(model_path=TFLITE_PATH)
        _interp.allocate_tensors()
        _inp = _interp.get_input_details()
        _out = _interp.get_output_details()
        _scaler_mean  = np.load(SCALER_MEAN)
        _scaler_scale = np.load(SCALER_SCALE)
        print(f"[LSTM-TFLite] Model loaded: {TFLITE_PATH}")
        return True
    except Exception as e:
        print(f"[LSTM-TFLite] Gagal load: {e}")
        _interp = None
        return False


def _load_csv():
    """Agregat volume rata-rata per bulan per area_type (PERSIS app.py)."""
    global DF_VOL_AGG, KEC_AREA
    if not os.path.exists(CSV_PATH):
        print(f"[LSTM-TFLite] CSV tidak ada: {CSV_PATH}")
        return

    # kumpulkan baris per area_type per (tahun,bulan)
    buckets = {}  # app_type → {(y,m): {sum, count}}
    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            csv_type = (row.get('area_type') or '').strip()
            app_type = AREA_TYPE_FROM_CSV.get(csv_type)
            if not app_type:
                continue
            try:
                y = int(row['tahun']); m = int(row['bulan'])
                vol = float(row['volume_ton'])
            except (ValueError, KeyError):
                continue
            kec = (row.get('kecamatan') or '').strip().upper()
            if kec:
                KEC_AREA[kec] = app_type
            b = buckets.setdefault(app_type, {})
            cell = b.setdefault((y, m), {'sum':0.0, 'count':0})
            cell['sum'] += vol; cell['count'] += 1

    # bangun timeline terurut + feature engineering
    for app_type, cells in buckets.items():
        rows = []
        for (y, m), agg in sorted(cells.items()):
            vol = agg['sum'] / agg['count']
            rows.append({
                'tahun': y, 'bulan': m, 'volume_ton': vol,
                'month_sin': math.sin(2*math.pi*m/12),
                'month_cos': math.cos(2*math.pi*m/12),
                'area_enc':  AREA_ENC[app_type],
                'year_norm': (y - YR_MIN) / (YR_MAX - YR_MIN),
            })
        # vol_ma3 = rolling mean window 3 (min_periods=1) — persis app.py
        for i in range(len(rows)):
            lo = max(0, i-2)
            window = [rows[j]['volume_ton'] for j in range(lo, i+1)]
            rows[i]['vol_ma3'] = sum(window) / len(window)
        DF_VOL_AGG[app_type] = rows

    print(f"[LSTM-TFLite] CSV agg: " +
          ", ".join(f"{k}={len(v)}bln" for k, v in DF_VOL_AGG.items()))


def init():
    """Panggil sekali saat startup FastAPI."""
    _load_csv()
    return _load_model()


def is_ready():
    return _interp is not None and bool(DF_VOL_AGG)


def _get_csv_vol(area_type, year, month):
    for r in DF_VOL_AGG.get(area_type, []):
        if r['tahun'] == year and r['bulan'] == month:
            return r['volume_ton']
    return None


def _rule_vol(area_type, year, month):
    return VOL_BASE.get(area_type, 2.0) * (1 + (year-2019)*0.035) * SEASONAL_FACTOR[month]


def _predict_forecast(area_type):
    """3 bulan forecast via LSTM TFLite. None kalau model/window tak tersedia."""
    if _interp is None:
        return None
    rows = DF_VOL_AGG.get(area_type)
    if not rows or len(rows) < WINDOW_SIZE:
        return None
    window = rows[-WINDOW_SIZE:]
    X = np.array([[r[c] for c in FEATURE_COLS] for r in window], dtype=np.float32)
    Xs = (X - _scaler_mean) / _scaler_scale
    Xb = np.expand_dims(Xs, axis=0).astype(np.float32)
    _interp.set_tensor(_inp[0]['index'], Xb)
    _interp.invoke()
    pred_scaled = _interp.get_tensor(_out[0]['index'])[0]
    # un-scale pakai mean/scale fitur ke-0 (volume_ton) — persis app.py
    pred_ton = pred_scaled * _scaler_scale[0] + _scaler_mean[0]
    return [max(0.0, float(v)) for v in pred_ton[:HORIZON]]


def predict_kecamatan(kecamatan):
    """Timeline 7 titik (3 history + current + 3 forecast) untuk satu kecamatan."""
    kec = kecamatan.strip().upper()
    area_type = KEC_AREA.get(kec)
    if area_type is None:
        return None

    now = datetime.now()
    by, bm = now.year, now.month
    timeline = []

    # 3 bulan lalu (CSV → fallback rule)
    for i in range(3, 0, -1):
        off = bm - i
        m, y = (12+off, by-1) if off <= 0 else (off, by)
        vol = _get_csv_vol(area_type, y, m)
        if vol is None: vol = _rule_vol(area_type, y, m)
        timeline.append({"bulan":m, "tahun":y, "volume_ton":round(vol,2),
                         "label":f"{LBL[m-1]} {y}", "type":"history"})

    # bulan ini
    vol_now = _get_csv_vol(area_type, by, bm) or _rule_vol(area_type, by, bm)
    timeline.append({"bulan":bm, "tahun":by, "volume_ton":round(vol_now,2),
                     "label":f"{LBL[bm-1]} {by}", "type":"current"})

    # forecast 3 bulan — LSTM kalau bisa, else rule
    forecasts = _predict_forecast(area_type)
    predictions = []
    for i in range(1, 4):
        off = bm + i
        y = by + (off-1)//12
        m = (off-1)%12 + 1
        if forecasts is not None and i-1 < len(forecasts):
            vol = forecasts[i-1]
        else:
            vol = _rule_vol(area_type, y, m)
        pt = {"bulan":m, "tahun":y, "volume_ton":round(vol,2),
              "label":f"{LBL[m-1]} {y}", "type":"forecast"}
        timeline.append(pt); predictions.append(pt)

    # history_12 (12 bulan terakhir <= sekarang) — persis app.py
    history_12 = []
    for r in DF_VOL_AGG.get(area_type, []):
        if r['tahun'] < by or (r['tahun'] == by and r['bulan'] <= bm):
            history_12.append({"bulan":r['bulan'], "tahun":r['tahun'],
                               "volume_ton":round(r['volume_ton'],2),
                               "label":f"{LBL[r['bulan']-1]} {r['tahun']}"})
    history_12 = history_12[-WINDOW_SIZE:]

    return {
        "kecamatan":   kec,
        "area_type":   area_type,
        "area_label":  AREA_LABEL[area_type],
        "model_used":  "LSTM+Attention (TFLite)" if (forecasts is not None) else "rule-based-fallback",
        "timeline":    timeline,
        "predictions": predictions,
        "history_12":  history_12,
    }
