"""
Gabungin beberapa dataset Roboflow (folder-structure & COCO) jadi SATU dataset
classification 3 kelas: Anorganik / B3 / Organik.

- Dataset "folder"  -> gambar per-kelas tinggal disalin ke kelas tujuan.
- Dataset "COCO"    -> tiap objek (bounding box) DI-CROP jadi gambar rapat,
                       biar mirip tampilan kamera SmartBIN (objek di tengah).

TIDAK ADA labeling manual. Script baca label yang sudah ada di dataset.

Cara pakai (di PC Windows lu):
  1. UNZIP semua dataset dulu. Taruh folder hasil unzip-nya di satu tempat, mis:
       C:\\smartbin\\raw\\WasteClassification.v1i.folder
       C:\\smartbin\\raw\\organic waste detection.v2i.coco
       ...dst
  2. Install sekali:  pip install pillow tqdm
  3. Edit RAW_DIR & OUT_DIR di bawah sesuai punya lu.
  4. Jalanin:  python merge_datasets.py
  5. Kalau ada kelas yang "TAK TERPETAKAN", nanti diprint di akhir — kabarin gw
     nama-namanya, atau tambahin sendiri ke KEYWORD_MAP / OVERRIDE.

Hasil akhir di OUT_DIR:
  smartbin-dataset/
    train/  {Anorganik, B3, Organik}/
    valid/  {Anorganik, B3, Organik}/
"""

import json, shutil, hashlib, collections
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    raise SystemExit("Install dulu:  pip install pillow tqdm")
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(x, **k):  # fallback tanpa progress bar
        return x

# ================== EDIT DI SINI ==================
RAW_DIR = Path(r"C:\smartbin\raw")          # folder berisi semua dataset hasil UNZIP
OUT_DIR = Path(r"C:\smartbin\smartbin-dataset")  # output gabungan
VAL_SPLIT_FALLBACK = 0.15  # kalau dataset gak punya folder valid, sisihin 15% buat valid
# =================================================

TARGET = ["Anorganik", "B3", "Organik"]

# Pemetaan otomatis berdasar KATA KUNCI pada nama kelas (huruf kecil, dicek "mengandung").
# Urutan penting: B3 dicek dulu (biar 'battery' gak kesangkut 'anorganik' dsb).
KEYWORD_MAP = [
    # (kategori tujuan, [kata kunci])
    ("B3", ["b3", "batter", "baterai", "spray", "bugs", "e-waste", "ewaste",
            "elektronik", "hazard", "toxic", "medis", "obat"]),
    ("Organik", ["organik", "organic", "daun", "leaf", "leave", "fruit", "buah",
                 "sayur", "veg", "food", "makanan", "kompos", "kulit", "ranting",
                 "wood", "kayu", "sisa"]),
    ("Anorganik", ["anorganik", "anorganic", "inorganic", "non-organik", "nonorganik",
                   "non organik", "plastic", "plastik", "botol", "bottle", "glass",
                   "kaca", "paper", "kertas", "metal", "logam", "kaleng", "can",
                   "tin", "besi", "karton", "cardboard", "styrofoam", "sterofoam"]),
]

# Override manual kalau kata kunci gak nangkep / salah. Isi: {"nama kelas asli (lowercase)": "Organik"}
OVERRIDE = {
    # "trash": "Anorganik",
}

unmapped = collections.Counter()

def map_class(name: str):
    n = name.strip().lower()
    if n in OVERRIDE:
        return OVERRIDE[n]
    for target, keys in KEYWORD_MAP:
        for k in keys:
            if k in n:
                return target
    unmapped[name] += 1
    return None

def uniq_name(src: Path, prefix: str) -> str:
    """Nama file unik biar gak tabrakan antar-dataset."""
    h = hashlib.md5(str(src).encode()).hexdigest()[:8]
    return f"{prefix}_{h}_{src.stem}{src.suffix.lower()}"

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
stats = collections.Counter()

def ensure_dirs():
    for split in ("train", "valid"):
        for c in TARGET:
            (OUT_DIR / split / c).mkdir(parents=True, exist_ok=True)

# ---------- Handler: dataset FOLDER-STRUCTURE ----------
def handle_folder(ds: Path, ds_name: str):
    # Struktur: ds/{train,valid,test}/<Kelas>/*.jpg  ATAU  ds/<Kelas>/*.jpg
    splits = [d for d in ds.iterdir() if d.is_dir() and d.name.lower() in
              ("train", "valid", "val", "validation", "test")]
    if not splits:  # tak ada split, kelas langsung di root
        splits = [ds]
    for sp in splits:
        split_out = "valid" if sp.name.lower() in ("valid", "val", "validation", "test") else "train"
        cls_dirs = [d for d in sp.iterdir() if d.is_dir()]
        for cd in cls_dirs:
            tgt = map_class(cd.name)
            if not tgt:
                continue
            imgs = [p for p in cd.rglob("*") if p.suffix.lower() in IMG_EXT]
            for img in tqdm(imgs, desc=f"{ds_name}:{cd.name}->{tgt}", leave=False):
                out = OUT_DIR / split_out / tgt / uniq_name(img, ds_name)
                try:
                    shutil.copy2(img, out)
                    stats[f"{split_out}/{tgt}"] += 1
                except Exception:
                    pass

# ---------- Handler: dataset COCO (crop bbox) ----------
def handle_coco(ds: Path, ds_name: str):
    # Struktur: ds/{train,valid,test}/_annotations.coco.json + gambar
    for sp in ds.iterdir():
        if not sp.is_dir():
            continue
        ann = sp / "_annotations.coco.json"
        if not ann.exists():
            continue
        split_out = "valid" if sp.name.lower() in ("valid", "val", "validation", "test") else "train"
        data = json.loads(ann.read_text(encoding="utf-8"))
        cats = {c["id"]: c["name"] for c in data.get("categories", [])}
        imgs = {im["id"]: im for im in data.get("images", [])}
        by_img = collections.defaultdict(list)
        for a in data.get("annotations", []):
            by_img[a["image_id"]].append(a)
        for img_id, anns in tqdm(by_img.items(), desc=f"{ds_name}:{sp.name}(coco)", leave=False):
            meta = imgs.get(img_id)
            if not meta:
                continue
            fp = sp / meta["file_name"]
            if not fp.exists():
                continue
            try:
                im = Image.open(fp).convert("RGB")
            except Exception:
                continue
            for k, a in enumerate(anns):
                tgt = map_class(cats.get(a["category_id"], ""))
                if not tgt:
                    continue
                x, y, w, h = a["bbox"]
                # perbesar sedikit (10%) biar konteks objek ikut, lalu clamp ke batas gambar
                pad_x, pad_y = w * 0.1, h * 0.1
                l = max(0, int(x - pad_x)); t = max(0, int(y - pad_y))
                r = min(im.width, int(x + w + pad_x)); b = min(im.height, int(y + h + pad_y))
                if r - l < 20 or b - t < 20:  # objek kekecilan, skip
                    continue
                crop = im.crop((l, t, r, b))
                out = OUT_DIR / split_out / tgt / f"{ds_name}_{img_id}_{k}_{tgt}.jpg"
                try:
                    crop.save(out, quality=90)
                    stats[f"{split_out}/{tgt}"] += 1
                except Exception:
                    pass

def is_coco(ds: Path) -> bool:
    return any((ds / s / "_annotations.coco.json").exists()
               for s in ("train", "valid", "test", "val", "validation"))

def main():
    if not RAW_DIR.exists():
        raise SystemExit(f"RAW_DIR gak ketemu: {RAW_DIR} — edit path-nya di script.")
    ensure_dirs()
    datasets = [d for d in RAW_DIR.iterdir() if d.is_dir()]
    if not datasets:
        raise SystemExit(f"Gak ada folder dataset di {RAW_DIR}. Udah di-UNZIP belum?")
    print(f"Nemu {len(datasets)} dataset di {RAW_DIR}\n")
    for ds in datasets:
        name = "".join(ch if ch.isalnum() else "-" for ch in ds.name)[:24]
        if is_coco(ds):
            print(f"[COCO]   {ds.name}")
            handle_coco(ds, name)
        else:
            print(f"[FOLDER] {ds.name}")
            handle_folder(ds, name)

    print("\n===== HASIL GABUNGAN =====")
    for c in TARGET:
        tr = stats.get(f"train/{c}", 0)
        va = stats.get(f"valid/{c}", 0)
        print(f"  {c:10s} train={tr:6d}  valid={va:5d}")
    total = sum(stats.values())
    print(f"  TOTAL gambar: {total}")

    if unmapped:
        print("\n⚠️  KELAS TAK TERPETAKAN (diabaikan) — kalau ada yang penting, kabarin gw"
              " atau tambahin ke OVERRIDE:")
        for name, n in unmapped.most_common():
            print(f"    '{name}'  ({n}x)")

    # Kalau ada kelas yang valid-nya kosong (dataset gak punya split valid),
    # sisihin sebagian train -> valid biar training punya data validasi.
    import random
    random.seed(42)
    for c in TARGET:
        vdir = OUT_DIR / "valid" / c
        tdir = OUT_DIR / "train" / c
        if not any(vdir.iterdir()) and any(tdir.iterdir()):
            files = list(tdir.glob("*"))
            k = int(len(files) * VAL_SPLIT_FALLBACK)
            for f in random.sample(files, k):
                shutil.move(str(f), str(vdir / f.name))
            print(f"  (auto-split) {c}: pindah {k} gambar train -> valid")

    print(f"\nSelesai. Dataset gabungan siap di: {OUT_DIR}")
    print("Set DATA_DIR di train_efficientnet_smartbin.py ke path itu, lalu training.")

if __name__ == "__main__":
    main()
