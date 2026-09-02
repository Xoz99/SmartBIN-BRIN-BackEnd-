#!/usr/bin/env python3
"""
Cari setelan ROI terbaik dari FOTO ASLI rig, bukan dari tebakan.

Alur:
  1. Di Pi, kumpulin frame penuh berlabel (mode bawaan main.py):
       DATASET_CAPTURE=1 DATASET_LABEL=organik python3 main.py   # taruh ~15 objek
       ...ulangi untuk anorganik & b3 (ganti label -> restart)
  2. Tarik ke laptop:
       scp -r brin@100.99.74.71:~/backend/dataset ./dataset
  3. Sweep:
       python3 sweep_roi.py --dataset dataset

Yang di-sweep: ROI_FRAC x ROI_DX x ROI_DY — persis parameter yang dipakai
_center_roi() di main.py, jadi hasilnya bisa langsung dipasang lewat env.

PENTING: dataset harus FRAME PENUH (seperti yang disimpan DATASET_CAPTURE),
bukan crop. Kalau sudah ke-crop, sweep-nya jadi tidak ada artinya.
"""
import argparse, os, sys, itertools
import numpy as np

try:
    import cv2
except ImportError:
    sys.exit("butuh opencv: pip install opencv-python")
try:
    from ai_edge_litert.interpreter import Interpreter
except ImportError:
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        sys.exit("butuh ai_edge_litert atau tflite_runtime")

CLASS = ["Anorganik", "B3", "Organik"]
# nama folder -> indeks kelas
FOLDER = {"anorganik": 0, "b3": 1, "organik": 2}


def load(path):
    it = Interpreter(model_path=path)
    it.allocate_tensors()
    return it, it.get_input_details(), it.get_output_details()


def infer(model, bgr):
    it, ins, outs = model
    h, w = int(ins[0]["shape"][1]), int(ins[0]["shape"][2])
    x = cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), (w, h)).astype(np.float32)
    it.set_tensor(ins[0]["index"], x[None])
    it.invoke()
    p = it.get_tensor(outs[0]["index"])[0].astype(np.float32)
    s = float(p.sum())
    return p / s if s else p


def roi_crop(frame, frac, dx, dy):
    """Salinan persis _center_roi() di main.py — kalau di sana berubah, samain."""
    h, w = frame.shape[:2]
    s = int(min(h, w) * max(0.1, min(1.0, frac)))
    x0 = int(w / 2 + dx * w) - s // 2
    y0 = int(h / 2 + dy * h) - s // 2
    x0 = max(0, min(w - s, x0))
    y0 = max(0, min(h - s, y0))
    return frame[y0:y0 + s, x0:x0 + s]


def muat_dataset(root):
    data = []
    for nama, idx in FOLDER.items():
        d = os.path.join(root, nama)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            im = cv2.imread(os.path.join(d, f))
            if im is not None:
                data.append((im, idx, f"{nama}/{f}"))
    return data


def putusan(p_pred, g_pred, gate, margin, floor):
    """Tiru keputusan _predict_vote() main.py: argmax utama, kecuali penjaga
    lolos gate+margin+veto -> dipaksa B3."""
    idx = int(np.argmax(p_pred))
    if g_pred is None:
        return idx
    g_b3 = float(g_pred[1])
    kedua = max(float(v) for i, v in enumerate(g_pred) if i != 1)
    if g_b3 >= gate and (margin <= 0 or g_b3 - kedua >= margin) \
       and (floor <= 0 or float(p_pred[1]) >= floor):
        return 1
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="dataset")
    ap.add_argument("--primary", default="model_ai_baru/an_or32.tflite")
    ap.add_argument("--guard", default="model_combo.tflite",
                    help="'' (kosong) = tanpa ensemble, nilai model utama saja")
    ap.add_argument("--gate", type=float, default=0.55)
    ap.add_argument("--margin", type=float, default=0.15)
    ap.add_argument("--floor", type=float, default=0.15)
    ap.add_argument("--frac", default="0.50:1.00:0.05")
    ap.add_argument("--dx", default="0.00:0.20:0.02")
    ap.add_argument("--dy", default="0.00:0.12:0.02")
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args()

    def rentang(spec):
        lo, hi, st = (float(v) for v in spec.split(":"))
        n = int(round((hi - lo) / st)) + 1
        return [round(lo + i * st, 4) for i in range(n)]

    data = muat_dataset(a.dataset)
    if not data:
        sys.exit(f"tidak ada gambar di '{a.dataset}/' (butuh subfolder organik/anorganik/b3)")
    jml = {c: 0 for c in CLASS}
    for _, y, _ in data:
        jml[CLASS[y]] += 1
    print(f"{len(data)} gambar: " + ", ".join(f"{k}={v}" for k, v in jml.items()))
    if min(jml.values()) < 5:
        print("[!] ada kelas < 5 gambar — hasilnya masih terlalu berisik buat dipercaya")

    prim = load(a.primary)
    guard = load(a.guard) if a.guard else None
    print(f"utama : {a.primary}\npenjaga: {a.guard or '(tanpa ensemble)'}\n")

    kombinasi = list(itertools.product(rentang(a.frac), rentang(a.dx), rentang(a.dy)))
    print(f"sweep {len(kombinasi)} setelan x {len(data)} gambar "
          f"= {len(kombinasi)*len(data)*(2 if guard else 1)} inferensi...\n")

    hasil = []
    for n, (frac, dx, dy) in enumerate(kombinasi, 1):
        benar = 0
        per_kelas = {i: [0, 0] for i in range(3)}   # [benar, total]
        bingung = np.zeros((3, 3), int)
        for im, y, _ in data:
            c = roi_crop(im, frac, dx, dy)
            pr = infer(prim, c)
            gr = infer(guard, c) if guard else None
            yhat = putusan(pr, gr, a.gate, a.margin, a.floor)
            bingung[y][yhat] += 1
            per_kelas[y][1] += 1
            if yhat == y:
                benar += 1
                per_kelas[y][0] += 1
        akurasi = benar / len(data)
        # rata-rata per kelas: jangan sampai menang cuma karena satu kelas dominan
        seimbang = float(np.mean([per_kelas[i][0] / per_kelas[i][1]
                                  for i in range(3) if per_kelas[i][1]]))
        hasil.append((seimbang, akurasi, frac, dx, dy, bingung))
        if n % 10 == 0 or n == len(kombinasi):
            print(f"\r  {n}/{len(kombinasi)}", end="", flush=True)
    print("\n")

    hasil.sort(key=lambda r: (-r[0], -r[1]))
    print(f"{'ROI_FRAC':>9} {'ROI_DX':>7} {'ROI_DY':>7} {'akurasi':>8} {'rata2/kelas':>12}")
    for s, ak, frac, dx, dy, _ in hasil[:a.top]:
        print(f"{frac:9.2f} {dx:7.3f} {dy:7.3f} {ak*100:7.1f}% {s*100:11.1f}%")

    s, ak, frac, dx, dy, bingung = hasil[0]
    print(f"\nTERBAIK → ROI_FRAC={frac:.2f} ROI_DX={dx:.3f} ROI_DY={dy:.3f}")
    print(f"  pasang: sudo systemctl set-environment ROI_FRAC={frac:.2f} ROI_DX={dx:.3f} ROI_DY={dy:.3f}")
    print("\n  confusion matrix (baris = sebenarnya, kolom = tebakan)")
    print("            " + "".join(f"{c:>11}" for c in CLASS))
    for i, c in enumerate(CLASS):
        print(f"  {c:>9} " + "".join(f"{bingung[i][j]:>11}" for j in range(3)))


if __name__ == "__main__":
    main()
