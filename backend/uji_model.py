"""
UJI MODEL berlabel — ngumpulin data buat nyetel ensemble pakai ANGKA, bukan feeling.

Alur: arahin objek ke kotak → tekan tombol kelas SEBENARNYA → script nyimpen
probabilitas PENUH dari model primary & guard. Setelah cukup sampel (Q),
dia cetak:
  1. Akurasi + confusion matrix tiap model.
  2. Cek URUTAN KELAS: nyoba 6 kemungkinan urutan output; kalau ada urutan lain
     yang jauh lebih akurat, berarti label model ketuker (bukan modelnya jelek).
  3. Sweep gate / margin / primary-floor: kombinasi mana yang paling sedikit
     salah, khususnya "organik ke-vote B3" (kasus daun).

Tombol:  O = Organik   A = Anorganik   3 = B3   |   U = undo sampel terakhir   Q = selesai

Jalanin:
  python uji_model.py                          # ambil sampel dari webcam
  python uji_model.py --analisa sampel.csv     # analisa ulang CSV lama, tanpa kamera
"""
import argparse
import csv
import itertools
import os
import time

import numpy as np

import sim_platform as sp

CLASS = sp.CLASS                       # ["Anorganik", "B3", "Organik"]
KEY2LABEL = {"a": "Anorganik", "3": "B3", "o": "Organik"}


def kumpulkan(primary_key, guard_key, vote, vote_delay, out_csv):
    import cv2
    p_path, g_path = sp.resolve_model(primary_key), sp.resolve_model(guard_key)
    p_it, p_in, p_out = sp.load_interp(p_path)
    g_it, g_in, g_out = sp.load_interp(g_path)
    print(f"[MODEL] primary : {p_path}")
    print(f"[MODEL] guard   : {g_path}")
    print("Taruh objek di kotak, lalu tekan kelas SEBENARNYA — O=Organik A=Anorganik 3=B3, U=undo, Q=selesai")

    cap = cv2.VideoCapture(0)
    data = []
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            roi, (x0, y0, s) = sp.center_roi(frame, sp.ROI_FRAC)
            cv2.rectangle(frame, (x0, y0), (x0 + s, y0 + s), (0, 220, 255), 2)
            jml = {c: sum(1 for d in data if d["label"] == c) for c in CLASS}
            cv2.putText(frame, f"sampel: " + "  ".join(f"{c}={jml[c]}" for c in CLASS),
                        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 255), 2)
            cv2.putText(frame, "O=Organik  A=Anorganik  3=B3  U=undo  Q=selesai",
                        (10, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
            cv2.imshow("UJI MODEL — tekan kelas sebenarnya", frame)

            k = chr(cv2.waitKey(1) & 0xFF).lower()
            if k == "q":
                break
            if k == "u" and data:
                buang = data.pop()
                print(f"  [undo] sampel {buang['label']} dihapus (sisa {len(data)})")
                continue
            if k in KEY2LABEL:
                label = KEY2LABEL[k]
                rois = sp.grab_rois(cap, vote, vote_delay) if vote > 1 else [roi]
                _, _, p_p = sp.classify_vote(p_it, p_in, p_out, rois or [roi])
                _, _, g_p = sp.classify_vote(g_it, g_in, g_out, rois or [roi])
                data.append({"label": label, "p": p_p, "g": g_p})
                print(f"  [{len(data):3d}] BENAR={label:10s} "
                      f"primary={CLASS[int(np.argmax(p_p))]:10s}({p_p.max()*100:3.0f}%) "
                      f"guard={CLASS[int(np.argmax(g_p))]:10s}({g_p.max()*100:3.0f}%) "
                      f"guardB3={g_p[CLASS.index('B3')]*100:3.0f}% primaryB3={p_p[CLASS.index('B3')]*100:3.0f}%")
    finally:
        cap.release()
        cv2.destroyAllWindows()

    if data:
        with open(out_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["label"] + [f"p_{c}" for c in CLASS] + [f"g_{c}" for c in CLASS])
            for d in data:
                w.writerow([d["label"]] + list(map(float, d["p"])) + list(map(float, d["g"])))
        print(f"\n[SIMPAN] {len(data)} sampel → {out_csv}")
    return data


def muat_csv(path):
    data = []
    with open(path) as f:
        for row in csv.DictReader(f):
            data.append({
                "label": row["label"],
                "p": np.array([float(row[f"p_{c}"]) for c in CLASS]),
                "g": np.array([float(row[f"g_{c}"]) for c in CLASS]),
            })
    return data


def confusion(data, key, perm=(0, 1, 2)):
    """perm = pemetaan index output model → CLASS. (0,1,2) = urutan yang diasumsikan."""
    m = {a: {b: 0 for b in CLASS} for a in CLASS}
    benar = 0
    for d in data:
        pred = CLASS[perm[int(np.argmax(d[key]))]]
        m[d["label"]][pred] += 1
        benar += pred == d["label"]
    return m, benar / len(data)


def cetak_confusion(m):
    print(f"      {'benar\\pred':<12}" + "".join(f"{c:>12}" for c in CLASS))
    for a in CLASS:
        print(f"      {a:<12}" + "".join(f"{m[a][b]:>12}" for b in CLASS))


def analisa(data, gate0, margin0, floor0):
    n = len(data)
    print("\n" + "=" * 78)
    print(f"HASIL — {n} sampel: " + "  ".join(
        f"{c}={sum(1 for d in data if d['label'] == c)}" for c in CLASS))

    for key, nama in (("p", "PRIMARY"), ("g", "GUARD")):
        m, acc = confusion(data, key)
        print("=" * 78)
        print(f"{nama}  akurasi = {acc*100:.1f}%")
        cetak_confusion(m)
        # Cek urutan kelas: kalau permutasi lain menang telak, label model ketuker.
        skor = sorted(((confusion(data, key, p)[1], p) for p in itertools.permutations(range(3))),
                      reverse=True)
        best_acc, best_perm = skor[0]
        if best_perm != (0, 1, 2) and best_acc - acc >= 0.15:
            urut = [CLASS[best_perm[i]] for i in range(3)]
            print(f"      ⚠ URUTAN KELAS CURIGA KETUKER: pakai urutan {urut} akurasi jadi "
                  f"{best_acc*100:.1f}% (naik {(best_acc-acc)*100:.0f} poin)")
        else:
            print(f"      urutan kelas ['Anorganik','B3','Organik'] = yang terbaik (aman)")

    # Sweep parameter gerbang B3.
    print("=" * 78)
    print("SWEEP GERBANG B3 (akurasi ensemble; 'org→B3' = organik yang salah di-vote B3)")
    print(f"      {'gate':>5} {'margin':>7} {'floor':>6} {'akurasi':>8} {'org→B3':>7} {'B3 kelewat':>11}")
    b3_i = CLASS.index("B3")
    hasil = []
    for gate in (0.55, 0.60, 0.70, 0.80, 0.90):
        for margin in (0.0, 0.15, 0.30):
            for floor in (0.0, 0.10, 0.15, 0.25, 0.35):
                benar = org_ke_b3 = b3_lolos = 0
                for d in data:
                    ok, *_ = sp._b3_should_override(d["g"], gate, margin, d["p"], floor)
                    pred = "B3" if ok else CLASS[int(np.argmax(d["p"]))]
                    benar += pred == d["label"]
                    if d["label"] != "B3" and pred == "B3":
                        org_ke_b3 += 1
                    if d["label"] == "B3" and pred != "B3":
                        b3_lolos += 1
                hasil.append((benar / len(data), -org_ke_b3, gate, margin, floor, org_ke_b3, b3_lolos))
    hasil.sort(reverse=True)
    for acc, _, gate, margin, floor, o2b, b3l in hasil[:8]:
        tandai = " ← setelan sekarang" if (gate, margin, floor) == (gate0, margin0, floor0) else ""
        print(f"      {gate:>5.2f} {margin:>7.2f} {floor:>6.2f} {acc*100:>7.1f}% {o2b:>7} {b3l:>11}{tandai}")
    acc, _, gate, margin, floor, o2b, b3l = hasil[0]
    print(f"\n  → paling bagus: --b3-gate {gate} --b3-margin {margin} --b3-primary-floor {floor} "
          f"(akurasi {acc*100:.1f}%, {o2b} salah-B3, {b3l} B3 kelewat)")
    print("=" * 78)


def main():
    ap = argparse.ArgumentParser(description="Ukur & setel ensemble pakai sampel berlabel")
    ap.add_argument("--ens-primary", default=sp.ENSEMBLE_PRIMARY_KEY, dest="primary")
    ap.add_argument("--ens-guard", default=sp.ENSEMBLE_GUARD_KEY, dest="guard")
    ap.add_argument("--vote", type=int, default=sp.VOTE_FRAMES_DEFAULT)
    ap.add_argument("--vote-delay", type=float, default=sp.VOTE_DELAY_DEFAULT, dest="vote_delay")
    ap.add_argument("--out", default="sampel.csv", help="file CSV hasil rekaman")
    ap.add_argument("--analisa", help="analisa CSV yang sudah ada (tanpa kamera)")
    ap.add_argument("--b3-gate", type=float, default=sp.B3_GATE_DEFAULT, dest="gate")
    ap.add_argument("--b3-margin", type=float, default=sp.B3_MARGIN_DEFAULT, dest="margin")
    ap.add_argument("--b3-primary-floor", type=float, default=sp.B3_PRIMARY_FLOOR_DEFAULT, dest="floor")
    a = ap.parse_args()

    data = muat_csv(a.analisa) if a.analisa else kumpulkan(
        a.primary, a.guard, a.vote, a.vote_delay, a.out)
    if not data:
        print("Belum ada sampel — nggak ada yang bisa dianalisa.")
        return
    analisa(data, a.gate, a.margin, a.floor)


if __name__ == "__main__":
    main()
