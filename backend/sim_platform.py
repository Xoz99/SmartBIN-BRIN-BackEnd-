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
  python sim_platform.py --compare        # jalanin lama + baru bareng, adu prediksi tiap deteksi
Preprocessing & urutan kelas SAMA dgn main.py (EfficientNet pass-through).
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
    "baru":   os.path.join(ROOT, "model_ai_baru", "model_fp16.tflite"),
    "baru32": os.path.join(ROOT, "model_ai_baru", "model_fp32.tflite"),
}


def resolve_model(name):
    """Terima keyword (lama/baru/baru32) atau path langsung ke file .tflite."""
    return MODELS.get(name, name)


def load_interp(path):
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


def center_roi(frame, frac):
    h, w = frame.shape[:2]
    s = int(min(h, w) * frac)
    cy, cx = h // 2, w // 2
    y0, x0 = max(0, cy - s // 2), max(0, cx - s // 2)
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
B3_GATE_DEFAULT = 0.55  # prob B3 model lama >= ini → hasil dipaksa B3
VOTE_FRAMES_DEFAULT = 5   # jumlah frame yg dirata-ratain (1 = matiin voting)
VOTE_DELAY_DEFAULT  = 0.03


def ensemble_predict(new_t, old_t, frame, gate):
    """Gabung dua model: model BARU jadi utama (unggul anorganik/organik),
    model LAMA jadi penjaga B3. Kalau model lama nge-vote B3 >= gate, hasil = B3;
    selain itu ikut model baru. Return (label, conf, probs, alasan)."""
    n_lbl, n_conf, n_p = classify(new_t[0], new_t[1], new_t[2], frame)
    o_lbl, o_conf, o_p = classify(old_t[0], old_t[1], old_t[2], frame)
    o_b3 = float(o_p[CLASS.index("B3")])
    if o_b3 >= gate:
        info = f"gerbang B3 → lama B3={o_b3*100:.0f}% >= {gate*100:.0f}% (baru bilang {n_lbl} {n_conf*100:.0f}%)"
        return "B3", o_b3, o_p, info
    info = f"ikut baru → {n_lbl} {n_conf*100:.0f}% (lama B3={o_b3*100:.0f}% < {gate*100:.0f}%)"
    return n_lbl, n_conf, n_p, info


def ensemble_vote(new_t, old_t, rois, gate):
    """Ensemble + voting: rata-ratain probabilitas model baru & prob B3 model lama
    dari beberapa frame, baru terapkan gerbang B3."""
    n_lbl, n_conf, n_p = classify_vote(new_t[0], new_t[1], new_t[2], rois)
    b3_i = CLASS.index("B3")
    o_b3 = float(np.mean([classify(old_t[0], old_t[1], old_t[2], r)[2][b3_i] for r in rois]))
    if o_b3 >= gate:
        info = f"gerbang B3 → lama B3={o_b3*100:.0f}% >= {gate*100:.0f}% (baru bilang {n_lbl} {n_conf*100:.0f}%)"
        return "B3", o_b3, n_p, info
    info = f"ikut baru → {n_lbl} {n_conf*100:.0f}% (lama B3={o_b3*100:.0f}% < {gate*100:.0f}%)"
    return n_lbl, n_conf, n_p, info


def live_loop(it, inp, out, model_path, cmp_it, cmp_inp, cmp_out, cmp_name,
              ensemble=False, gate=B3_GATE_DEFAULT,
              vote=VOTE_FRAMES_DEFAULT, vote_delay=VOTE_DELAY_DEFAULT):
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
                    (it, inp, out), (cmp_it, cmp_inp, cmp_out), rois, gate)
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
        tag = (f"LIVE ENSEMBLE  baru + lama(B3>={gate*100:.0f}%)" if ensemble
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
    ap = argparse.ArgumentParser(description="Simulasi EcoSort + tes model AI")
    ap.add_argument("--model", default="baru",
                    help="lama | baru | baru32 | path ke .tflite (default: baru)")
    ap.add_argument("--compare", action="store_true",
                    help="jalanin model lama + baru bareng, cetak dua prediksi tiap deteksi")
    ap.add_argument("--live", action="store_true",
                    help="mode tes model: klasifikasi terus-menerus tanpa nunggu aktuator")
    ap.add_argument("--ensemble", action="store_true",
                    help="gabung: model baru utama (anor/organik) + model lama penjaga B3")
    ap.add_argument("--b3-gate", type=float, default=B3_GATE_DEFAULT, dest="b3_gate",
                    help=f"ambang prob B3 model lama utk override ke B3 (default {B3_GATE_DEFAULT})")
    ap.add_argument("--vote", type=int, default=VOTE_FRAMES_DEFAULT,
                    help=f"jumlah frame dirata-ratain per keputusan, 1=matiin (default {VOTE_FRAMES_DEFAULT})")
    ap.add_argument("--vote-delay", type=float, default=VOTE_DELAY_DEFAULT, dest="vote_delay",
                    help=f"jeda antar-frame voting, detik (default {VOTE_DELAY_DEFAULT})")
    args = ap.parse_args()

    # Ensemble: utama = model baru, pembanding/penjaga = model lama.
    if args.ensemble:
        args.model = "baru"

    model_path = resolve_model(args.model)
    it, inp, out = load_interp(model_path)
    print(f"[MODEL] aktif: {os.path.relpath(model_path, ROOT)}")

    # Muat model pembanding (buat --compare) atau penjaga B3 (buat --ensemble).
    cmp_it = cmp_inp = cmp_out = cmp_name = None
    if args.compare or args.ensemble:
        other = "lama" if args.model != "lama" else "baru"
        cmp_path = resolve_model(other)
        cmp_it, cmp_inp, cmp_out = load_interp(cmp_path)
        cmp_name = os.path.relpath(cmp_path, ROOT)
        role = "penjaga B3" if args.ensemble else "pembanding"
        print(f"[MODEL] {role}: {cmp_name}")
    if args.ensemble:
        print(f"[ENSEMBLE] baru utama, lama override B3 kalau prob B3 >= {args.b3_gate*100:.0f}%")

    if args.live:
        live_loop(it, inp, out, model_path, cmp_it, cmp_inp, cmp_out, cmp_name,
                  ensemble=args.ensemble, gate=args.b3_gate,
                  vote=args.vote, vote_delay=args.vote_delay)
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
                        if args.ensemble:
                            label, conf, _, info = ensemble_predict(
                                (it, inp, out), (cmp_it, cmp_inp, cmp_out), frame, args.b3_gate)
                            last = (label, conf)
                            print(f"[CAM] ✓ {label} ({conf*100:.0f}%) → AKTUASI (simulasi jatuhin)")
                            print(f"      [ensemble] {info}")
                        else:
                            label, conf, _ = classify(it, inp, out, frame)
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
