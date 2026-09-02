"""
SIMULASI SISTEM di laptop — logika mode PLATFORM yang SAMA dgn main.py Pi,
pakai webcam laptop + model_advanced.tflite. Lihat alur sistem tanpa hardware:

  Platform KOSONG (baseline) → objek masuk kotak → auto jepret + klasifikasi
  → "AKTUASI" (simulasi jatuhin) → tunggu aktuator → siap objek berikutnya

Kontrol:  B = ambil ulang baseline (pas kosong)  |  Q = keluar
Jalankan:
  python sim_platform.py                 # default: model BARU (model_ai_baru/model_fp16.tflite)
  python sim_platform.py --model lama     # model lama (model_advanced.tflite)
  python sim_platform.py --model baru32   # model baru presisi fp32
  python sim_platform.py --model keras    # model .keras langsung (butuh TensorFlow)
  python sim_platform.py --model path/ke/model.keras       # atau path bebas
  python sim_platform.py --compare        # jalanin lama + baru bareng, adu prediksi tiap deteksi
  python sim_platform.py --ensemble       # ensemble: primary & guard default (lihat ENSEMBLE_PRIMARY_KEY/ENSEMBLE_GUARD_KEY)
  python sim_platform.py --ensemble --ens-primary rpp32 --ens-guard baru3   # override kombinasi ensemble lewat CLI
Preprocessing & urutan kelas SAMA dgn main.py (EfficientNet pass-through) —
berlaku untuk .tflite maupun .keras, jadi perbandingannya adil.
"""
import argparse
import os
import time
import cv2
import numpy as np

# Root repo = folder di atas backend/ (biar path model tetap ketemu dari mana pun dijalanin).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = {
    "lama":   os.path.join(ROOT, "model_advanced.tflite"),
    "lama2":  os.path.join(ROOT, "model_ai_baru", "model_fp16.tflite"),
    "baru":   os.path.join(ROOT, "backend", "model_fp16(3).tflite"),
    "baru21":   os.path.join(ROOT, "backend", "model_fp16_v5_daunasli.tflite"),
    "baru1":   os.path.join(ROOT, "backend", "model_fp16(2).tflite"),
    "baru2":   os.path.join(ROOT, "backend", "model_fp16.tflite"),
    "baru3":   os.path.join(ROOT, "backend", "model_combo.tflite"),
    "baru32": os.path.join(ROOT, "model_ai_baru", "model_fp32.tflite"),
    "rpp32": os.path.join(ROOT, "model_ai_baru", "an_or32.tflite"),
    "rpp16": os.path.join(ROOT, "backend", "rapip16.tflite"),
    # HATI-HATI: ada 3 file berbeda bernama model_advanced.tflite (root, backend/,
    # model_ai_baru/) dengan md5 berbeda. "adv" = yang di model_ai_baru (konversi
    # terbaru dari model_sampah_Advanced.keras), "lama" = yang di root.
    "adv":    os.path.join(ROOT, "model_ai_baru", "model_advanced.tflite"),
    "keras":  os.path.join(ROOT, "model_ai_baru", "best_model.keras"),
    "keras2": os.path.join(ROOT, "backend", "model_keras_ai", "model_sampah_Advanced.keras"),
    "lunak":  os.path.join(ROOT, "model_ai_baru", "model_advanced.tflite"),

}

# Default kombinasi ensemble kalau --ens-primary / --ens-guard tidak dikasih.
# Bisa di-override lewat CLI tanpa ubah kode, buat A/B test kombinasi lain.
ENSEMBLE_PRIMARY_KEY = "lama"
ENSEMBLE_GUARD_KEY    = "rpp32"


def resolve_model(name):
    """Terima keyword (lama/baru/keras/...) atau path langsung ke file model
    (.tflite, .keras, atau .h5)."""
    return MODELS.get(name, name)


def _custom_layers(tf):
    """Layer custom yang dipakai model tim AI (model_sampah_Advanced.keras =
    EfficientNet-B3 + channel attention). Tanpa ini load_model gagal dengan
    "Could not locate class 'ChannelAttentionLayer'".

    Definisi disalin dari 'backend/versi utk desktop/main.py:69' — kalau di sana
    berubah, samain di sini, kalau tidak bobot model ter-load ke arsitektur yang
    salah dan prediksinya diam-diam ngaco.
    """
    layers = tf.keras.layers

    class ChannelAttentionLayer(layers.Layer):
        def __init__(self, reduction_ratio=16, **kwargs):
            super().__init__(**kwargs)
            self.reduction_ratio = reduction_ratio

        def build(self, input_shape):
            ch = input_shape[-1]
            self.gap = layers.GlobalAveragePooling2D()
            self.gmp = layers.GlobalMaxPooling2D()
            self.fc1 = layers.Dense(max(1, ch // self.reduction_ratio),
                                    activation="relu", use_bias=False)
            self.fc2 = layers.Dense(ch, activation="sigmoid", use_bias=False)
            super().build(input_shape)

        def call(self, x):
            avg_w = self.fc2(self.fc1(self.gap(x)))
            max_w = self.fc2(self.fc1(self.gmp(x)))
            ch    = tf.shape(x)[-1]
            scale = tf.reshape(avg_w + max_w, [-1, 1, 1, ch])
            return x * scale

        def get_config(self):
            cfg = super().get_config()
            cfg.update({"reduction_ratio": self.reduction_ratio})
            return cfg

    return {"ChannelAttentionLayer": ChannelAttentionLayer}


class KerasShim:
    """Bungkus model Keras biar antarmukanya sama persis dgn tflite Interpreter.

    Alasannya: classify(), classify_vote(), dan jalur ensemble semuanya bicara
    dalam bahasa set_tensor/invoke/get_tensor. Dengan shim ini nol baris di sana
    yang perlu diubah, dan .keras vs .tflite jadi bisa diadu apple-to-apple.

    TensorFlow di-import di dalam __init__, bukan di atas file — biar yang cuma
    pakai .tflite tidak kena beban import TF (berat, ~10 dtk).
    """

    def __init__(self, path):
        try:
            import tensorflow as tf
        except ImportError:
            raise SystemExit(
                f"'{os.path.basename(path)}' butuh TensorFlow (model .keras belum dikonversi).\n"
                f"  pasang:  pip install tensorflow\n"
                f"  atau pakai versi .tflite-nya lewat --model <keyword lain>"
            )
        self.model = tf.keras.models.load_model(
            path, compile=False, custom_objects=_custom_layers(tf))
        ish = self.model.input_shape         # (None, H, W, 3)
        osh = self.model.output_shape        # (None, n_kelas)
        # quantization (0.0, 0) = tidak terkuantisasi; dibaca oleh pemanggil yg
        # meniru _run() main.py.
        self._in = {"index": 0, "shape": [1, int(ish[1]), int(ish[2]), int(ish[3])],
                    "dtype": np.float32, "quantization": (0.0, 0)}
        self._out = {"index": 0, "shape": [1, int(osh[-1])],
                     "dtype": np.float32, "quantization": (0.0, 0)}
        self._x = None
        self._y = None

    def allocate_tensors(self):
        pass

    def get_input_details(self):
        return [self._in]

    def get_output_details(self):
        return [self._out]

    def set_tensor(self, index, value):
        self._x = value

    def invoke(self):
        self._y = self.model.predict(self._x, verbose=0)

    def get_tensor(self, index):
        return self._y


def load_interp(path):
    """Muat model apa pun (.tflite / .keras / .h5) → (interp, input_detail, output_detail)."""
    if not os.path.exists(path):
        raise SystemExit(f"model tidak ditemukan: {path}")
    if path.lower().endswith((".keras", ".h5")):
        it = KerasShim(path)
    else:
        it = Interpreter(model_path=path)
    it.allocate_tensors()
    return it, it.get_input_details()[0], it.get_output_details()[0]

try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        import tensorflow as tf
        Interpreter = tf.lite.Interpreter
from PIL import Image

# ── Config (sama dgn main.py; ubah di sini buat coba-coba) ──
ROI_FRAC     = 0.6
# Geser pusat ROI (fraksi lebar/tinggi frame) — ada di main.py buat kamera Pi yang
# tidak lurus di atas buletan. Di webcam laptop biasanya 0, tapi tetap disediakan
# supaya sim bisa niru setelan Pi persis: --roi-frac 0.74 --roi-dx 0.12 --roi-dy 0.065
ROI_DX       = 0.0
ROI_DY       = 0.0
OBJECT_DIFF  = 18.0   # beda dari kosong utk dianggap ada objek
CLEAR_DIFF   = 9.0
STILL_MOVE   = 4.0    # gerak antar-frame di bawah ini = objek diam
STILL_NEED   = 3
REARM_BUFFER = 1.5
CONF_THRESHOLD = 0.0  # 0 = tanpa batas (sama default main.py)
CAMERA_WARMUP_SEC = 2.0  # kamera settle dulu sebelum ambil baseline (cegah gerak sendiri di awal)
ACT_TIME = {"organik": 7.0, "anorganik": 8.0, "b3": 9.5}

CLASS = ["Anorganik", "B3", "Organik"]
COLOR = {"Anorganik": (153, 124, 91), "B3": (94, 90, 209), "Organik": (108, 132, 72)}  # BGR


def center_roi(frame, frac, dx=None, dy=None):
    """Salinan _center_roi() main.py (termasuk clamp biar kotak tidak keluar frame).
    Kalau yang di main.py berubah, samain di sini — kalau tidak, sim berhenti
    memprediksi Pi dan semua perbandingan jadi menyesatkan."""
    h, w = frame.shape[:2]
    s = int(min(h, w) * max(0.1, min(1.0, frac)))
    dx = ROI_DX if dx is None else dx
    dy = ROI_DY if dy is None else dy
    x0 = int(w / 2 + dx * w) - s // 2
    y0 = int(h / 2 + dy * h) - s // 2
    x0 = max(0, min(w - s, x0))
    y0 = max(0, min(h - s, y0))
    return frame[y0:y0 + s, x0:x0 + s], (x0, y0, s)


def classify(interp, inp, out, frame_bgr):
    h, w = int(inp["shape"][1]), int(inp["shape"][2])
    img = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)).resize((w, h))
    x = np.expand_dims(np.array(img, dtype=np.float32), 0)
    interp.set_tensor(inp["index"], x)
    interp.invoke()
    p = interp.get_tensor(out["index"])[0].astype(float)
    i = int(np.argmax(p))
    return CLASS[i], float(p[i]), p


def grab_rois(cap, n, delay):
    """Ambil n frame beruntun (jeda `delay`) → list ROI. Niru voting main.py."""
    rois = []
    for _ in range(max(1, n)):
        ok, fr = cap.read()
        if ok:
            roi, _ = center_roi(fr, ROI_FRAC)
            rois.append(roi)
        if delay > 0:
            time.sleep(delay)
    return rois


def classify_vote(interp, inp, out, rois):
    """Rata-ratain probabilitas beberapa ROI → 1 keputusan (voting antar-frame)."""
    acc = None
    for roi in rois:
        _, _, p = classify(interp, inp, out, roi)
        acc = p if acc is None else acc + p
    p = acc / len(rois)
    i = int(np.argmax(p))
    return CLASS[i], float(p[i]), p


LIVE_EVERY = 1.2      # jeda antar klasifikasi di mode --live (detik)
# Gate DISAMAIN dgn B3_GATE main.py (0.55) supaya sim memprediksi Pi. Dulu di sini
# 0.70 karena daun kering bikin model lama ngasih B3 56-66%; masalah itu sekarang
# ditangani margin + primary-floor, bukan dengan naikin gate. Mau balik ke perilaku
# lama: --b3-gate 0.70
B3_GATE_DEFAULT = 0.55   # prob B3 model guard >= ini → kandidat override B3
# Margin tambahan: prob B3 model guard harus menang telak dari kelas kedua-tertingginya
# (bukan cuma lewat gate tipis-tipis), biar B3 "ragu-ragu" tidak maksa override.
B3_MARGIN_DEFAULT = 0.15
# Lantai (veto) primary: guard cuma boleh maksa B3 kalau model primary SETUJU
# minimal segini di kelas B3. Guard (model_combo) ternyata bisa ngasih B3 93-96%
# buat daun — gate/margin nggak nyaring itu karena dua-duanya cuma ngukur si guard
# sendiri. Butuh suara kedua: kalau primary bilang B3 cuma ~beberapa persen,
# override dibatalin. Set 0 buat matiin veto (perilaku lama).
B3_PRIMARY_FLOOR_DEFAULT = 0.15
VOTE_FRAMES_DEFAULT = 5   # jumlah frame yg dirata-ratain (1 = matiin voting)
VOTE_DELAY_DEFAULT  = 0.03
# Cara dua model digabung — sama persis dgn ENSEMBLE_MODE di main.py:
#   guard     = argmax primary, guard cuma boleh MEMAKSA jadi B3 (perilaku lama)
#   avg       = rata-rata tertimbang probabilitas dua model, baru gerbang B3
#   confident = ikut model yang probabilitas tertingginya lebih besar
ENS_MODE_DEFAULT    = "guard"
GUARD_WEIGHT_DEFAULT = 0.5


def _gabung(n_p, o_p, mode, w):
    """Gabungkan probabilitas primary (n_p) & guard (o_p) sesuai mode.
    Return (probs_gabungan, keterangan_asal)."""
    if mode == "avg":
        w = max(0.0, min(1.0, w))
        return n_p * (1.0 - w) + o_p * w, f"avg(w={w:.2f})"
    if mode == "confident":
        pakai_guard = float(np.max(o_p)) > float(np.max(n_p))
        return (o_p, "guard") if pakai_guard else (n_p, "primary")
    return n_p, "primary"


def _b3_should_override(o_p, gate, margin, n_p=None, primary_floor=0.0):
    """Putusin apakah hasil dipaksa jadi B3. Tiga syarat, semua harus lolos:
      (a) prob B3 guard >= gate
      (b) B3 guard menang >= margin dari kelas kedua-tertingginya
      (c) prob B3 primary >= primary_floor  (veto: butuh suara kedua)
    Return (lolos, o_b3, second_max, n_b3, daftar_alasan_gagal)."""
    b3_i = CLASS.index("B3")
    o_b3 = float(o_p[b3_i])
    others = [float(v) for i, v in enumerate(o_p) if i != b3_i]
    second_max = max(others) if others else 0.0
    n_b3 = float(n_p[b3_i]) if n_p is not None else None

    gagal = []
    if o_b3 < gate:
        gagal.append(f"guard B3 {o_b3*100:.0f}% < gate {gate*100:.0f}%")
    if o_b3 - second_max < margin:
        gagal.append(f"margin {(o_b3 - second_max)*100:.0f}% < {margin*100:.0f}%")
    if n_b3 is not None and primary_floor > 0 and n_b3 < primary_floor:
        gagal.append(f"primary B3 {n_b3*100:.0f}% < floor {primary_floor*100:.0f}% (VETO)")
    return (not gagal), o_b3, second_max, n_b3, gagal


def _info_override(o_b3, second_max, n_b3, gate, margin, n_lbl, n_conf):
    return (f"gerbang B3 → guard B3={o_b3*100:.0f}% (gate {gate*100:.0f}%, "
            f"margin {(o_b3 - second_max)*100:.0f}%>={margin*100:.0f}%"
            + (f", primary B3={n_b3*100:.0f}%" if n_b3 is not None else "")
            + f") (primary bilang {n_lbl} {n_conf*100:.0f}%)")


def _info_primary(o_b3, n_lbl, n_conf, n_b3, gagal, sumber="primary"):
    # Prob B3 primary ikut dicetak di dua-dua cabang — itu angka yang dipakai
    # buat nyetel --b3-primary-floor dari data lapangan, bukan tebak-tebakan.
    pb = f", primary B3={n_b3*100:.0f}%" if n_b3 is not None else ""
    return (f"ikut {sumber} → {n_lbl} {n_conf*100:.0f}%  "
            f"[guard B3={o_b3*100:.0f}%{pb} ditolak: " + "; ".join(gagal) + "]")


def ensemble_predict(new_t, old_t, frame, gate, margin=B3_MARGIN_DEFAULT,
                     primary_floor=B3_PRIMARY_FLOOR_DEFAULT,
                     mode=ENS_MODE_DEFAULT, guard_weight=GUARD_WEIGHT_DEFAULT):
    """Gabung dua model: model PRIMARY jadi utama (unggul anorganik/organik),
    model GUARD jadi penjaga B3. Override ke B3 HANYA kalau prob B3 guard
    >= gate DAN menang telak (margin) dari kelas kedua-tertingginya; selain itu
    ikut model primary. Return (label, conf, probs, alasan)."""
    n_lbl, n_conf, n_p = classify(new_t[0], new_t[1], new_t[2], frame)
    o_lbl, o_conf, o_p = classify(old_t[0], old_t[1], old_t[2], frame)
    if mode != "guard":
        n_p, _ = _gabung(n_p, o_p, mode, guard_weight)
        i = int(np.argmax(n_p))
        n_lbl, n_conf = CLASS[i], float(n_p[i])
    override, o_b3, second_max, n_b3, gagal = _b3_should_override(
        o_p, gate, margin, n_p, primary_floor)
    if override:
        return "B3", o_b3, o_p, _info_override(o_b3, second_max, n_b3, gate, margin, n_lbl, n_conf)
    return n_lbl, n_conf, n_p, _info_primary(o_b3, n_lbl, n_conf, n_b3, gagal)


def ensemble_vote(new_t, old_t, rois, gate, margin=B3_MARGIN_DEFAULT,
                  primary_floor=B3_PRIMARY_FLOOR_DEFAULT,
                  mode=ENS_MODE_DEFAULT, guard_weight=GUARD_WEIGHT_DEFAULT):
    """Ensemble + voting: rata-ratain probabilitas model primary & probabilitas
    PENUH model guard (bukan cuma B3) dari beberapa frame, baru terapkan
    gerbang B3 (gate + margin) atas rata-rata itu."""
    n_lbl, n_conf, n_p = classify_vote(new_t[0], new_t[1], new_t[2], rois)
    old_acc = None
    for r in rois:
        _, _, p = classify(old_t[0], old_t[1], old_t[2], r)
        old_acc = p if old_acc is None else old_acc + p
    o_p = old_acc / len(rois)

    # Gabung dulu (kalau modenya bukan "guard"), gerbang B3 jalan di ATAS hasil
    # gabungan — urutan ini sama persis dgn _predict_vote() main.py.
    asal = "primary"
    if mode != "guard":
        p_awal, l_awal, c_awal = n_p, n_lbl, n_conf
        n_p, asal = _gabung(n_p, o_p, mode, guard_weight)
        i = int(np.argmax(n_p))
        n_lbl, n_conf = CLASS[i], float(n_p[i])
        print(f"[ENS ] {mode} → {n_lbl} {n_conf*100:.0f}% via {asal} "
              f"[primary {l_awal} {c_awal*100:.0f}% | guard {CLASS[int(np.argmax(o_p))]} "
              f"{float(np.max(o_p))*100:.0f}%]")

    override, o_b3, second_max, n_b3, gagal = _b3_should_override(
        o_p, gate, margin, n_p, primary_floor)
    if override:
        return "B3", o_b3, n_p, _info_override(o_b3, second_max, n_b3, gate, margin, n_lbl, n_conf)
    return n_lbl, n_conf, n_p, _info_primary(
        o_b3, n_lbl, n_conf, n_b3, gagal, "primary" if mode == "guard" else f"gabungan({mode})")


def live_loop(it, inp, out, model_path, cmp_it, cmp_inp, cmp_out, cmp_name,
              ensemble=False, gate=B3_GATE_DEFAULT, margin=B3_MARGIN_DEFAULT,
              primary_floor=B3_PRIMARY_FLOOR_DEFAULT,
              vote=VOTE_FRAMES_DEFAULT, vote_delay=VOTE_DELAY_DEFAULT,
              mode=ENS_MODE_DEFAULT, guard_weight=GUARD_WEIGHT_DEFAULT):
    """Mode tes model: arahin objek ke kotak, prediksi jalan terus tanpa aktuator."""
    cap = cv2.VideoCapture(0)
    next_at = 0.0
    last = None      # (label, conf, probs)
    cmp_last = None  # (label, conf)
    vtag = f"voting {vote} frame" if vote > 1 else "1 frame (no voting)"
    print("MODE LIVE — arahin objek ke kotak, prediksi update tiap "
          f"{LIVE_EVERY:.1f}s [{vtag}]. Q=keluar.")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        roi, (x0, y0, s) = center_roi(frame, ROI_FRAC)
        now = time.time()

        if now >= next_at:
            next_at = now + LIVE_EVERY
            # Ambil beberapa frame beruntun untuk voting (niru main.py).
            rois = grab_rois(cap, vote, vote_delay) if vote > 1 else [roi]
            if not rois:
                rois = [roi]
            if ensemble:
                label, conf, probs, info = ensemble_vote(
                    (it, inp, out), (cmp_it, cmp_inp, cmp_out), rois, gate, margin,
                    primary_floor, mode, guard_weight)
                last = (label, conf, probs)
                print(f"[ENS ] {label:10s} ({conf*100:3.0f}%)   {info}")
            else:
                label, conf, probs = classify_vote(it, inp, out, rois)
                last = (label, conf, probs)
                line = "  ".join(f"{c}={probs[i]*100:4.0f}%" for i, c in enumerate(CLASS))
                print(f"[LIVE] {label:10s} ({conf*100:3.0f}%)   {line}")
                if cmp_it is not None:
                    c_label, c_conf, _ = classify_vote(cmp_it, cmp_inp, cmp_out, rois)
                    cmp_last = (c_label, c_conf)
                    print(f"       [adu] {cmp_name}: {c_label} ({c_conf*100:.0f}%)"
                          f"  → {'SAMA' if c_label == label else 'BEDA'}")

        cv2.rectangle(frame, (x0, y0), (x0 + s, y0 + s), (0, 220, 255), 2)
        if last:
            lbl, cf, _ = last
            c = COLOR.get(lbl, (200, 200, 200))
            cv2.putText(frame, f"{lbl} {cf*100:.0f}%", (x0, max(28, y0 - 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, c, 2)
        tag = (f"LIVE ENSEMBLE [{mode}]  primary + guard(B3>={gate*100:.0f}%)" if ensemble
               else f"LIVE  model: {os.path.basename(model_path)}")
        cv2.putText(frame, tag,
                    (10, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 220, 255), 1)
        if cmp_last:
            cv2.putText(frame, f"adu {cmp_name}: {cmp_last[0]} {cmp_last[1]*100:.0f}%",
                        (10, frame.shape[0] - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        cv2.imshow("SIMULASI EcoSort — LIVE tes model (Q=keluar)", frame)
        if (cv2.waitKey(1) & 0xFF) == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()


def main():
    # ROI dipakai lewat global (center_roi & grab_rois baca dari sini), jadi
    # deklarasinya harus di atas sebelum ROI_FRAC dibaca sebagai default argparse.
    global ROI_FRAC, ROI_DX, ROI_DY

    ap = argparse.ArgumentParser(description="Simulasi EcoSort + tes model AI")
    ap.add_argument("--model", default="baru",
                    help="lama | baru | baru32 | path ke .tflite (default: baru)")
    ap.add_argument("--compare", action="store_true",
                    help="jalanin model lama + baru bareng, cetak dua prediksi tiap deteksi")
    ap.add_argument("--live", action="store_true",
                    help="mode tes model: klasifikasi terus-menerus tanpa nunggu aktuator")
    ap.add_argument("--ensemble", action="store_true",
                    help="gabung 2 model: primary (anor/organik) + guard B3 (override B3). "
                         "Kombinasi diatur lewat --ens-primary/--ens-guard.")
    ap.add_argument("--ens-primary", default=ENSEMBLE_PRIMARY_KEY, dest="ens_primary",
                    help=f"key/path model PRIMARY saat --ensemble (default {ENSEMBLE_PRIMARY_KEY})")
    ap.add_argument("--ens-guard", default=ENSEMBLE_GUARD_KEY, dest="ens_guard",
                    help=f"key/path model GUARD B3 saat --ensemble (default {ENSEMBLE_GUARD_KEY})")
    ap.add_argument("--b3-gate", type=float, default=B3_GATE_DEFAULT, dest="b3_gate",
                    help=f"ambang prob B3 model guard utk override ke B3 (default {B3_GATE_DEFAULT})")
    ap.add_argument("--b3-margin", type=float, default=B3_MARGIN_DEFAULT, dest="b3_margin",
                    help=f"margin minimum prob B3 vs kelas kedua-tertinggi model guard (default {B3_MARGIN_DEFAULT})")
    ap.add_argument("--b3-primary-floor", type=float, default=B3_PRIMARY_FLOOR_DEFAULT,
                    dest="b3_primary_floor",
                    help=f"prob B3 minimum model PRIMARY biar override guard diterima; "
                         f"0=matiin veto (default {B3_PRIMARY_FLOOR_DEFAULT})")
    ap.add_argument("--vote", type=int, default=VOTE_FRAMES_DEFAULT,
                    help=f"jumlah frame dirata-ratain per keputusan, 1=matiin (default {VOTE_FRAMES_DEFAULT})")
    ap.add_argument("--vote-delay", type=float, default=VOTE_DELAY_DEFAULT, dest="vote_delay",
                    help=f"jeda antar-frame voting, detik (default {VOTE_DELAY_DEFAULT})")
    ap.add_argument("--ens-mode", default=ENS_MODE_DEFAULT, dest="ens_mode",
                    choices=["guard", "avg", "confident"],
                    help=f"cara gabung 2 model, sama dgn ENSEMBLE_MODE main.py "
                         f"(default {ENS_MODE_DEFAULT})")
    ap.add_argument("--guard-weight", type=float, default=GUARD_WEIGHT_DEFAULT,
                    dest="guard_weight",
                    help=f"bobot guard di --ens-mode avg (default {GUARD_WEIGHT_DEFAULT})")
    ap.add_argument("--roi-frac", type=float, default=ROI_FRAC, dest="roi_frac",
                    help=f"sisi ROI, fraksi sisi terpendek frame (default {ROI_FRAC})")
    ap.add_argument("--roi-dx", type=float, default=ROI_DX, dest="roi_dx",
                    help="geser pusat ROI, fraksi LEBAR frame, + = kanan (default 0)")
    ap.add_argument("--roi-dy", type=float, default=ROI_DY, dest="roi_dy",
                    help="geser pusat ROI, fraksi TINGGI frame, + = bawah (default 0)")
    args = ap.parse_args()

    ROI_FRAC, ROI_DX, ROI_DY = args.roi_frac, args.roi_dx, args.roi_dy
    print(f"[ROI] frac={ROI_FRAC:.2f} dx={ROI_DX:+.3f} dy={ROI_DY:+.3f}")

    # Ensemble: primary/guard sekarang bisa dituker lewat CLI (--ens-primary/--ens-guard)
    # tanpa ubah kode, buat A/B test kombinasi model dengan cepat.
    if args.ensemble:
        args.model = args.ens_primary

    model_path = resolve_model(args.model)
    it, inp, out = load_interp(model_path)
    print(f"[MODEL] aktif: {os.path.relpath(model_path, ROOT)}")

    # Muat model pembanding (buat --compare) atau penjaga B3 (buat --ensemble).
    cmp_it = cmp_inp = cmp_out = cmp_name = None
    if args.compare or args.ensemble:
        if args.ensemble:
            other = args.ens_guard
        else:
            other = "lama" if args.model != "lama" else "baru"
        cmp_path = resolve_model(other)
        cmp_it, cmp_inp, cmp_out = load_interp(cmp_path)
        cmp_name = os.path.relpath(cmp_path, ROOT)
        role = "penjaga B3" if args.ensemble else "pembanding"
        print(f"[MODEL] {role}: {cmp_name}")
    if args.ensemble:
        print(f"[ENSEMBLE] mode={args.ens_mode}"
              + (f" (bobot guard {args.guard_weight:.2f})" if args.ens_mode == "avg" else ""))
        print(f"[ENSEMBLE] primary={args.ens_primary}, guard B3={args.ens_guard} "
              f"(override B3 kalau prob B3 guard >= gate {args.b3_gate*100:.0f}%, "
              f"menang >= margin {args.b3_margin*100:.0f}% dari kelas kedua-tertinggi, "
              + (f"DAN prob B3 primary >= floor {args.b3_primary_floor*100:.0f}%)"
                 if args.b3_primary_floor > 0 else "veto primary MATI)"))

    if args.live:
        live_loop(it, inp, out, model_path, cmp_it, cmp_inp, cmp_out, cmp_name,
                  ensemble=args.ensemble, gate=args.b3_gate, margin=args.b3_margin,
                  primary_floor=args.b3_primary_floor,
                  vote=args.vote, vote_delay=args.vote_delay,
                  mode=args.ens_mode, guard_weight=args.guard_weight)
        return

    cap = cv2.VideoCapture(0)
    baseline = None
    prev = None
    armed = True
    still = 0
    rearm_at = 0.0
    last = None  # (label, conf)
    warmup_until = time.time() + CAMERA_WARMUP_SEC

    print("SIMULASI mode PLATFORM. Pastikan KOTAK kosong dulu (baseline). B=re-baseline, Q=keluar.")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        roi, (x0, y0, s) = center_roi(frame, ROI_FRAC)
        gray = cv2.GaussianBlur(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), (21, 21), 0)
        now = time.time()

        # ── WARMUP: kamera settle dulu, belum deteksi (baseline diambil setelah ini) ──
        if now < warmup_until:
            cv2.rectangle(frame, (x0, y0), (x0 + s, y0 + s), (200, 200, 200), 2)
            cv2.putText(frame, f"MENYALA... warmup {warmup_until - now:.0f}s (kotak kosong)",
                        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
            cv2.imshow("SIMULASI EcoSort — B=baseline, Q=keluar", frame)
            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                break
            baseline = None  # baseline diambil dari frame pertama SETELAH warmup
            continue

        obj_diff = move = 0.0
        if baseline is None:
            baseline = gray
            prev = gray
        else:
            obj_diff = float(cv2.absdiff(gray, baseline).mean())
            move = float(cv2.absdiff(gray, prev).mean())
            prev = gray

            if armed:
                if obj_diff > OBJECT_DIFF and move < STILL_MOVE:
                    still += 1
                    if still >= STILL_NEED:
                        # Input model = crop ROI, bukan frame penuh — samain dgn
                        # CLASSIFY_ROI di main.py. Kalau di sini frame penuh, sim
                        # ngasih hasil yang beda dari Pi buat gambar yang sama.
                        if args.ensemble:
                            label, conf, _, info = ensemble_predict(
                                (it, inp, out), (cmp_it, cmp_inp, cmp_out), roi,
                                args.b3_gate, args.b3_margin, args.b3_primary_floor,
                                args.ens_mode, args.guard_weight)
                            last = (label, conf)
                            print(f"[CAM] ✓ {label} ({conf*100:.0f}%) → AKTUASI (simulasi jatuhin)")
                            print(f"      [ensemble] {info}")
                        else:
                            label, conf, _ = classify(it, inp, out, roi)
                            last = (label, conf)
                            if conf >= CONF_THRESHOLD:
                                print(f"[CAM] ✓ {label} ({conf*100:.0f}%) → AKTUASI (simulasi jatuhin)")
                            if cmp_it is not None:
                                c_label, c_conf, _ = classify(cmp_it, cmp_inp, cmp_out, frame)
                                match = "SAMA" if c_label == label else "BEDA"
                                print(f"      [adu] {cmp_name}: {c_label} ({c_conf*100:.0f}%)  → {match}")
                        armed = False
                        still = 0
                        rearm_at = now + ACT_TIME.get(label.lower(), 8.0) + REARM_BUFFER
                else:
                    still = 0
            else:
                if now >= rearm_at:
                    armed = True
                    baseline = gray
                    print("[CAM] siap objek berikutnya.")

        # ── Gambar ──
        if armed:
            box_col = (108, 132, 72)   # hijau: siap
            status = "SIAP - taruh objek di kotak"
        else:
            wait = max(0, rearm_at - now)
            box_col = (94, 90, 209)    # merah: aktuator jalan
            status = f"AKTUASI... siap dalam {wait:.0f}s (angkat objek)"
        cv2.rectangle(frame, (x0, y0), (x0 + s, y0 + s), box_col, 2)
        cv2.putText(frame, status, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, box_col, 2)
        cv2.putText(frame, f"obj_diff={obj_diff:5.1f} (butuh>{OBJECT_DIFF:.0f})  move={move:4.1f}",
                    (10, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        model_tag = f"model: {os.path.basename(model_path)}" + ("  [ADU]" if cmp_it is not None else "")
        cv2.putText(frame, model_tag, (10, frame.shape[0] - 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 255), 1)
        if last:
            lbl, cf = last
            c = COLOR.get(lbl, (200, 200, 200))
            cv2.putText(frame, f"Terakhir: {lbl} {cf*100:.0f}%", (10, 58),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, c, 2)

        cv2.imshow("SIMULASI EcoSort — B=baseline, Q=keluar", frame)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k == ord("b"):
            baseline = gray
            armed = True
            still = 0
            print("[CAM] baseline di-reset (kotak dianggap kosong).")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()