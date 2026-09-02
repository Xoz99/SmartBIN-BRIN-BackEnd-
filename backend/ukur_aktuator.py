#!/usr/bin/env python3
"""Ukur waktu SIKLUS AKTUATOR sebenarnya, langsung dari serial STM32.

Kenapa perlu: ACTUATOR_SEC_ORGANIK/ANORGANIK/B3 di .env itu cuma nentuin berapa
lama KAMERA nunggu sebelum siap objek berikutnya. Angkanya selama ini ditebak.
Kalau ternyata mekanik butuh lebih lama dari tebakan, kamera keburu armed dan
ngirim perintah baru di tengah gerakan — piringan jadi tidak pernah sampai home.

Skrip ini ngirim SATU perintah, lalu nyatat tiap baris balasan STM32 beserta
detik ke-berapa dia muncul. Dari situ ketahuan durasi asli tiap kategori.

PENTING: main.py harus DIMATIKAN dulu (port serial cuma bisa dipegang satu proses).
PERINGATAN: skrip ini MENGGERAKKAN mekanik. Pastikan tidak ada tangan/objek nyangkut.

Pakai:
    python3 ukur_aktuator.py anorganik
    python3 ukur_aktuator.py organik --tunggu 20
    python3 ukur_aktuator.py b3 --ulang 3      # 3x biar keliatan konsisten/nggak
    python3 ukur_aktuator.py reset             # cuma homing, tanpa milah
"""
import argparse
import sys
import time

import serial
import serial.tools.list_ports

# TEMUAN 2026-09-02: firmware bin-003 TIDAK punya penanda selesai yang bisa dipakai.
# "[Aktuator] Auto-reset -> 0 derajat" sempat dikira balasan perintah, ternyata
# dipancarkan BERKALA sendiri — terbukti muncul saat kita dengerin 30 detik tanpa
# mengirim apa pun. Memakainya sebagai penanda bikin FALSE POSITIVE: perintah yang
# sebenarnya diabaikan terlihat "berhasil dalam 0,59 detik".
#
# Jadi daftar ini sengaja DIKOSONGKAN. Skrip ini sekarang cuma merekam apa adanya;
# penentuan berhasil/tidak HARUS dari mengamati mekaniknya langsung.
# Isi lagi HANYA kalau firmware nanti diubah supaya membalas per-perintah
# (mis. cetak "[Aktuator] SELESAI <kategori>" tepat setelah gerakan kelar).
PENANDA_SELESAI = ()


def cari_stm32():
    """Cari port STM32 dari VID/deskripsi USB — sama logikanya dgn main.py."""
    for p in serial.tools.list_ports.comports():
        blob = f"{p.description} {p.manufacturer or ''} {p.hwid or ''}".lower()
        if any(k in blob for k in ("stmicro", "stm32", "virtual com", "0483")):
            return p.device
    return None


def satu_siklus(ser, perintah: str, tunggu: float) -> float | None:
    """Kirim 1 perintah, cetak tiap balasan + detiknya. Balikin durasi sampai
    penanda selesai muncul, atau None kalau tidak pernah muncul."""
    ser.reset_input_buffer()
    t0 = time.time()
    ser.write((perintah + "\n").encode())
    ser.flush()
    print(f"  [ 0.00s] >>> KIRIM: {perintah}")

    durasi = None
    while time.time() - t0 < tunggu:
        raw = ser.readline()
        if not raw:
            continue
        baris = raw.decode("utf-8", errors="ignore").strip()
        if not baris:
            continue
        t = time.time() - t0
        # Baris JSON telemetri panjang & tidak relevan buat timing → ringkas saja.
        tampil = baris if len(baris) <= 100 else baris[:97] + "..."
        print(f"  [{t:5.2f}s] {tampil}")
        if durasi is None and any(k in baris.lower() for k in PENANDA_SELESAI):
            durasi = t
            print(f"  [{t:5.2f}s] ^^^ MEKANIK SELESAI (butuh {t:.2f} detik)")
    return durasi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("perintah", choices=["organik", "anorganik", "b3", "B3", "reset"])
    ap.add_argument("--port", default=None, help="default: auto-detect STM32")
    ap.add_argument("--tunggu", type=float, default=15.0, help="detik dengerin tiap siklus")
    ap.add_argument("--ulang", type=int, default=1, help="ulang N kali biar keliatan konsisten")
    ap.add_argument("--jeda", type=float, default=5.0, help="detik jeda antar pengulangan")
    a = ap.parse_args()

    port = a.port or cari_stm32()
    if not port:
        sys.exit("STM32 tidak ketemu. Colok board-nya, atau kasih --port /dev/ttyACM1")

    try:
        ser = serial.Serial(port, 115200, timeout=1)
    except serial.SerialException as e:
        sys.exit(f"Gagal buka {port}: {e}\n"
                 f"Kalau 'Device or resource busy' → matikan main.py dulu (Ctrl+C).")

    print(f"Port  : {port} @ 115200")
    print(f"Kirim : {a.perintah}  ({a.ulang}x, dengerin {a.tunggu}s tiap siklus)")
    print("PERINGATAN: mekanik akan bergerak.\n")
    time.sleep(2)   # kasih waktu board settle setelah port dibuka (DTR bisa nge-reset)

    hasil = []
    for i in range(1, a.ulang + 1):
        print(f"--- siklus {i}/{a.ulang} ---")
        d = satu_siklus(ser, a.perintah, a.tunggu)
        hasil.append(d)
        if d is None:
            print(f"  (!) penanda selesai TIDAK muncul dalam {a.tunggu:.0f}s — "
                  f"mekanik mungkin nyangkut, atau firmware tidak melaporkannya")
        if i < a.ulang:
            time.sleep(a.jeda)
        print()

    ser.close()

    ok = [d for d in hasil if d is not None]
    print("=" * 56)
    if not PENANDA_SELESAI:
        print("Perintah sudah dikirim ke serial. Firmware ini TIDAK membalas per-perintah,")
        print("jadi skrip tidak bisa menyimpulkan berhasil/gagal — LIHAT MEKANIKNYA langsung.")
        print("Bergerak = perintah diterima. Diam = diabaikan (atau mekanik macet).")
        return
    if not ok:
        print("Tidak ada siklus yang selesai. Ini indikasi kuat masalah di MEKANIK/FIRMWARE,")
        print("bukan di timing Raspi — Pi cuma ngirim 1 baris teks dan tidak pernah dibalas selesai.")
        return
    print(f"Durasi  : {', '.join(f'{d:.2f}s' for d in ok)}")
    print(f"Terlama : {max(ok):.2f}s")
    if len(ok) > 1 and max(ok) - min(ok) > 1.0:
        print(f"(!) Selisih {max(ok) - min(ok):.2f}s antar siklus — mekaniknya TIDAK konsisten.")
        print("    Timing yang tidak stabil begini biasanya slip/kehilangan step di stepper,")
        print("    bukan sesuatu yang bisa dibenerin dengan nyetel angka di .env.")
    saran = max(ok) + 1.5
    kunci = {"organik": "ACTUATOR_SEC_ORGANIK", "anorganik": "ACTUATOR_SEC_ANORGANIK",
             "b3": "ACTUATOR_SEC_B3"}.get(a.perintah.lower())
    if kunci:
        print(f"\nSaran .env : {kunci}={saran:.1f}   (terlama + margin 1.5s)")
        print("             REARM_BUFFER biarkan 1.5")


if __name__ == "__main__":
    main()
