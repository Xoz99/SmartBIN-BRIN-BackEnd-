import io
import os
import asyncio
import json
import ssl
import queue
import threading
import time
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import numpy as np
from PIL import Image
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from google import genai
import paho.mqtt.client as mqtt_client
import serial
import serial.tools.list_ports

from remote_control import RemoteControl

# Baca file .env (di folder yang sama) kalau ada → config per-node tanpa ubah kode
# maupun ketik env panjang tiap run. Cukup `python3 main.py`. (pip install python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ========================================================
# SETUP TFLITE RUNTIME
# ========================================================
try:
    import ai_edge_litert.interpreter as tflite
    _HAS_TF = False
except ImportError:
    try:
        import tflite_runtime.interpreter as tflite
        _HAS_TF = False
    except ImportError:
        import tensorflow.lite as tflite
        _HAS_TF = True

if _HAS_TF:
    from tensorflow.keras.applications.efficientnet import preprocess_input as eff_preprocess
else:
    eff_preprocess = None

# ========================================================
# CONFIG
# ========================================================
# Model DASAR: dipakai sendirian kalau ENSEMBLE=0, atau sebagai PENJAGA B3 kalau
# ensemble aktif. Bisa ditukar lewat env tanpa ubah kode (samain dgn MODEL_PATH_NEW),
# mis. MODEL_PATH=model_ai_baru/model_advanced.tflite. Path relatif dicari juga di
# folder atas lewat _find_model().
MODEL_PATH   = os.getenv("MODEL_PATH", "model_combo.tflite")
FRONTEND_DIR = "../frontend"
CLASS_NAMES  = ["Anorganik", "B3", "Organik"]

# --- Ensemble klasifikasi ---
# Model BARU (primary) jadi utama (lebih akurat anorganik/organik: model_ai_baru/an_or32.tflite),
# model LAMA (guard) jadi "penjaga B3" (model_combo.tflite): kalau guard nge-vote
# B3 >= B3_GATE, hasil akhir dipaksa B3. Ini nutupin kelemahan primary yg sering
# meleset di B3. Kombinasi ini sudah divalidasi manual lewat sim_platform.py
# (--ensemble --ens-primary rpp32 --ens-guard baru3) sebelum dipindah ke sini.
# Aktif hanya kalau file model baru ketemu; kalau tidak, jatuh ke model tunggal
# (perilaku lama) — jadi aman kalau di Raspi file barunya belum dicopy.
ENSEMBLE       = os.getenv("ENSEMBLE", "1") not in ("0", "false", "False", "")
MODEL_PATH_NEW = os.getenv("MODEL_PATH_NEW", "model_ai_baru/an_or32.tflite")
B3_GATE        = float(os.getenv("B3_GATE", "0.55"))
# Dua syarat tambahan, diport dari sim_platform.py setelah kasus "daun ke-vote B3":
# gate doang cuma ngukur si guard sendiri, jadi guard yg PD salah (prob B3 93-96%
# buat daun) lolos gampang. B3_MARGIN maksa B3 menang telak dari kelas kedua di
# guard; B3_PRIMARY_FLOOR minta suara kedua — model utama harus setuju minimal
# segini di B3. Set 0 buat matiin masing-masing (balik ke perilaku gate-doang).
B3_MARGIN        = float(os.getenv("B3_MARGIN", "0.15"))
B3_PRIMARY_FLOOR = float(os.getenv("B3_PRIMARY_FLOOR", "0.15"))
# Cara dua model digabung:
#   "guard"     — argmax model utama; penjaga cuma boleh MEMAKSA jadi B3 (default,
#                 perilaku lama). Kelemahannya: pendapat penjaga soal Organik vs
#                 Anorganik DIBUANG, walaupun sering dia yang benar. Contoh nyata:
#                 lakban -> utama bilang Organik 53%, penjaga Anorganik 99.9%, yang
#                 dipakai malah yang salah.
#   "avg"       — rata-rata tertimbang probabilitas kedua model, lalu argmax.
#                 Penjaga B3 tetap jalan di atasnya.
#   "confident" — ikut model yang probabilitas tertingginya lebih besar.
ENSEMBLE_MODE = os.getenv("ENSEMBLE_MODE", "guard").strip().lower()
GUARD_WEIGHT  = float(os.getenv("GUARD_WEIGHT", "0.5"))   # bobot penjaga di mode "avg"
# Voting antar-frame: ambil beberapa frame lalu rata-ratain probabilitasnya biar
# keputusan lebih stabil (mengurangi loncat organik<->anorganik, mis. daun kering).
# VOTE_FRAMES=1 → matiin voting (perilaku lama, 1 frame).
VOTE_FRAMES    = max(1, int(os.getenv("VOTE_FRAMES", "5")))
VOTE_DELAY     = float(os.getenv("VOTE_DELAY", "0.03"))   # jeda antar-frame (detik)

# --- SERIAL PORT STM32 ---
# LoRa dibuang 2026-09-03 (modulnya dicabut biar tidak ikut narik daya). Yang
# tersisa cuma STM32, dideteksi dari deskripsi/VID USB supaya tidak salah port
# waktu nomor ttyACM* geser tiap reboot.
#   STM32 = STMicroelectronics / Virtual COM / VID 0483
# Override paksa lewat env STM32_PORT kalau deteksi meleset.
STM32_PORT = os.environ.get("STM32_PORT")   # None → auto-detect
BAUD_RATE  = int(os.environ.get("BAUD_RATE", "115200"))

# --- Backend SmartBIN (MQTT HiveMQ Cloud) — set per-node lewat env var. ---
# NODE_ID WAJIB unik tiap Raspberry Pi/bin (mis. bin-001, bin-002, ...).
# Jalankan misalnya: NODE_ID=bin-005 python3 main.py
MQTT_HOST    = os.environ.get("MQTT_HOST", "4b1ed76fd60640648c995b6c90f11829.s1.eu.hivemq.cloud")
MQTT_PORT    = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USER    = os.environ.get("MQTT_USER", "bintrash")
MQTT_PASS    = os.environ.get("MQTT_PASS", "Smartbinbrin1")
NODE_ID      = os.environ.get("NODE_ID", "bin-003")

# Bridge remote-control lewat MQTT (topik smartbin/{NODE_ID}/device/*).
# Bikin start/stop kamera + baca status + tail log bisa dari luar NAT tanpa VPN.
remote = RemoteControl(NODE_ID)

# --- Jalur forward telemetri sensor ---
# Telemetri sensor SELALU dikirim ke MQTT (→ backend VPS). Saklar FORWARD_MQTT
# sudah dibuang 2026-09-09. Dulu dia dipakai buat eksperimen banding LoRa vs HTTP,
# dan LoRa sendiri sudah dicabut 2026-09-03 — jadi saklar itu tinggal jadi jebakan:
# `FORWARD_MQTT=0` yang ketinggalan di .env bikin SELURUH telemetri sensor (isi bin,
# baterai, berat, GPS) hilang diam-diam tanpa satu pun pesan error, sementara laporan
# klasifikasi tetap jalan lewat jalur lain — jadi sekilas kelihatan normal.
# Kalau perlu dimatikan lagi, matikan di sisi backend/broker, jangan di sini.

# Log sensor ringkas: cetak tiap N bacaan (1=tiap bacaan, 5=lebih sepi). Kurangi spam.
LOG_SENSOR_EVERY = int(os.environ.get("LOG_SENSOR_EVERY", "1"))
LOG_RAW_SERIAL = os.environ.get("LOG_RAW_SERIAL", "1") not in ("0", "false", "")  # cetak baris MENTAH dari STM32 (sebelum diparse) apa adanya


def _sensor_summary(d: dict) -> str:
    """Ringkasan 1-baris bacaan STM32 (bukan dump JSON kepotong yang bikin rancu).
    'organik/anorganik/b3' di sini = KOMPARTEMEN sensor, BUKAN hasil deteksi kamera."""
    try:
        vols = "/".join(str((d.get(c) or {}).get("volume", "-")) for c in ("organik", "anorganik", "b3"))
        berat = d.get("berat_g", "-")
        batt = (d.get("battery") or {}).get("percent", "-")
        gps = "fix" if (d.get("sat") or 0) > 0 else "-"
        return f"berat={berat}g laci(O/A/B3)={vols}% batt={batt}% gps={gps}"
    except Exception:
        return ""

# --- Jalur HTTP langsung ke server (opsional) ---
# COMPARE_HTTP=1 → tiap bacaan STM32 JUGA di-POST langsung ke server
# (transport=http), selain lewat MQTT. seq+sentAt disuntik ke tiap bacaan supaya
# backend bisa hitung packet loss (dari seq) & latency (createdAt−sentAt).
# Dulu jalur ini dipakai buat membandingkan LoRa vs HTTP; LoRa sudah dicabut
# 2026-09-03, jadi sekarang tinggal jalur pembanding MQTT.
COMPARE_HTTP      = os.environ.get("COMPARE_HTTP", "0") == "1"
BACKEND_HTTP_URL  = os.environ.get("BACKEND_HTTP_URL", "http://192.168.1.23:3000").rstrip("/")
DEVICE_INGEST_KEY = os.environ.get("DEVICE_INGEST_KEY", "")
INGEST_URL        = f"{BACKEND_HTTP_URL}/ingest/sensor"

# Topik harus SAMA PERSIS dengan yang di-subscribe backend (src/mqtt/topics.js):
#   smartbin/+/sensor, smartbin/+/status, smartbin/+/classification
TOPIC_SENSOR         = f"smartbin/{NODE_ID}/sensor"
TOPIC_STATUS         = f"smartbin/{NODE_ID}/status"
TOPIC_CMD            = f"smartbin/{NODE_ID}/cmd"
TOPIC_CLASSIFICATION = f"smartbin/{NODE_ID}/classification"  # <- lapor hasil pemilahan ke backend

# ========================================================
# GLOBAL STATE
# ========================================================
arduino        = None   # koneksi serial ke STM32
latest_sensor  = {}
sensor_lock    = threading.Lock()
mqtt_connected = False
_mqtt_client   = None
_is_running    = False
_dispatcher_thread = None
_seq_counter   = 0      # nomor urut paket (buat deteksi packet loss di backend)
_stm32_port    = None   # path port STM32 yang dipakai (buat watchdog buka ulang)
_t_serial_ok   = 0.0    # kapan port STM32 terakhir dibuka (patokan umur data)
_wd_resets     = 0      # berapa kali watchdog sudah reset serial
_wd_last_reset = 0.0    # kapan terakhir reset (epoch)
_http_q        = None   # antrean POST jalur HTTP (queue.Queue) — non-blocking
_http_session  = None   # requests.Session buat jalur HTTP

# ========================================================
# INIT SERIAL — SEKALI SAJA, DI SATU TEMPAT
# ========================================================
def _detect_port_stm32():
    """Cari port STM32 dari deskripsi/VID USB (STMicroelectronics / VID 0483)."""
    for p in serial.tools.list_ports.comports():
        blob = f"{p.description} {p.manufacturer or ''} {getattr(p, 'product', '') or ''} {p.hwid or ''}".lower()
        if any(k in blob for k in ("stmicro", "stm32", "virtual com", "0483")):
            return p.device
    return None


def init_all_serial():
    """
    Buka koneksi serial STM32 satu kali di awal startup.
    Port ditentukan berjenjang: env STM32_PORT > auto-deteksi USB > /dev/ttyACM0.
    Auto-deteksi mencegah salah port waktu nomor ttyACM* geser tiap reboot.
    """
    global arduino, _stm32_port, _t_serial_ok

    auto_stm = _detect_port_stm32()
    stm_port = STM32_PORT or auto_stm or "/dev/ttyACM0"
    _stm32_port = stm_port   # disimpan supaya watchdog bisa buka ulang port yang sama
    print(f"[Serial] Deteksi USB → STM32={auto_stm or '-'} | dipakai: {stm_port}")

    try:
        arduino = serial.Serial(stm_port, BAUD_RATE, timeout=1)
        time.sleep(2)
        _t_serial_ok = time.time()
        print(f"[+] STM32 terhubung di {stm_port} @ {BAUD_RATE}")
    except Exception as e:
        arduino = None
        print(f"[!] Gagal buka STM32 di {stm_port}: {e}")


def close_all_serial():
    global arduino
    if arduino is not None and arduino.is_open:
        arduino.close()
        print("[+] Serial STM32 ditutup.")

# ========================================================
# WATCHDOG SERIAL — pulihin STM32 yang hang tanpa dicabut-colok manual
# ========================================================
# Masalah nyata 2026-09-02: STM32 berhenti total di tengah jalan. Kamera dan
# klasifikasi tetap mulus, tapi TIDAK ADA aktuasi sama sekali — dan tidak ada
# tanda apa pun: /status tetap bilang serial_stm32 "connected", karena itu cuma
# mengecek port kebuka di level OS. Board yang hang tetap tampil "connected".
# arduino.write() juga tetap "sukses" (byte cuma masuk buffer OS).
#
# Yang membangunkan board waktu itu: MEMBUKA ULANG portnya. Membuka port USB CDC
# menegaskan DTR, dan itu me-reset STM32 — ketahuan karena tiap kali skrip tes
# dijalankan (buka port sendiri) mekaniknya langsung nurut lagi.
#
# Jadi watchdog ini meniru hal itu: kalau tidak ada satu pun data masuk selama
# SERIAL_QUIET_SEC, tutup lalu buka lagi portnya. Board reboot, telemetri jalan
# lagi, pemilahan lanjut — tanpa ada yang perlu menyentuh perangkat.
#
# CATATAN JUJUR: ini menambal GEJALA. Penyebab hang-nya ada di firmware STM32
# yang source-nya tidak ada di repo ini, jadi tidak bisa diperbaiki dari sini.
SERIAL_WATCHDOG  = os.environ.get("SERIAL_WATCHDOG", "1") not in ("0", "false", "False", "")
SERIAL_QUIET_SEC = float(os.environ.get("SERIAL_QUIET_SEC", "30"))   # sepi selama ini = dianggap hang
SERIAL_RESET_GAP = float(os.environ.get("SERIAL_RESET_GAP", "25"))   # jeda minimal antar reset


def umur_data_stm32() -> float:
    """Detik sejak paket TERAKHIR dari STM32, atau inf kalau BELUM PERNAH ada
    satu paket pun sejak proses ini hidup.

    Sengaja inf, bukan 'dihitung sejak port dibuka'. Port kebuka BUKAN bukti
    board ngirim data: 2026-09-03 /dev/ttyACM0 muncul normal dan bisa dibuka,
    tapi firmware-nya diam total (0 baris dalam 45 detik, dan 3 perintah gerak
    tidak dibalas maupun dieksekusi). Patokan lama bikin gerbang lapor
    'stm32_siap: true' selama 90 detik pertama padahal board-nya mati."""
    with sensor_lock:
        ts = (latest_sensor or {}).get("_timestamp")
    return float("inf") if ts is None else time.time() - ts


def sepi_stm32() -> float:
    """Khusus watchdog: sama seperti di atas, tapi kalau belum ada paket
    dihitung sejak port dibuka — biar board dikasih waktu boot dulu, bukan
    langsung di-reset berulang begitu proses nyala."""
    with sensor_lock:
        ts = (latest_sensor or {}).get("_timestamp")
    return time.time() - (ts or _t_serial_ok)


def reset_serial_stm32(alasan: str = "") -> bool:
    """Tutup lalu buka ulang port STM32 (memicu reset board lewat DTR)."""
    global arduino, _t_serial_ok, _wd_resets, _wd_last_reset
    if not _stm32_port:
        return False

    lama, arduino = arduino, None      # dispatcher berhenti pakai port ini dulu
    time.sleep(0.3)
    try:
        if lama is not None:
            lama.close()
    except Exception as e:
        print(f"[Watchdog] Gagal nutup port (dilanjut): {e}")

    time.sleep(1.0)                    # kasih waktu USB CDC benar-benar lepas
    try:
        baru = serial.Serial(_stm32_port, BAUD_RATE, timeout=1)
        time.sleep(2)                  # board perlu waktu boot setelah DTR reset
        arduino = baru
        _t_serial_ok  = time.time()
        _wd_resets   += 1
        _wd_last_reset = time.time()
        print(f"[Watchdog] Serial STM32 dibuka ulang ({alasan}) — reset ke-{_wd_resets}. "
              f"Board mestinya boot lagi; tunggu telemetri masuk.")
        return True
    except Exception as e:
        print(f"[Watchdog] GAGAL buka ulang {_stm32_port}: {e}")
        return False


def _watchdog_loop():
    print(f"[Watchdog] Aktif — reset serial kalau sepi > {SERIAL_QUIET_SEC:.0f} detik.")
    while _is_running:
        time.sleep(2)
        if arduino is None:
            continue
        umur = sepi_stm32()
        if umur < SERIAL_QUIET_SEC:
            continue
        if time.time() - _wd_last_reset < SERIAL_RESET_GAP:
            continue               # baru saja reset, kasih kesempatan board boot
        print(f"[Watchdog] STM32 sepi {umur:.0f} detik — board kemungkinan hang, reset serial...")
        reset_serial_stm32(f"sepi {umur:.0f}s")
    print("[Watchdog] Berhenti.")

# ========================================================
# SATU DISPATCHER THREAD — baca STM32, forward ke MQTT (+ HTTP kalau dinyalain)
# ========================================================
def _serial_dispatcher_loop():
    global latest_sensor, _seq_counter
    print("[Dispatcher] Thread dimulai.")
    while _is_running:
        if arduino is None or not arduino.is_open:
            time.sleep(1)
            continue
        try:
            if arduino.in_waiting > 0:
                raw_line = arduino.readline().decode("utf-8", errors="ignore").strip()
                if not raw_line:
                    continue

                # RAW: tampilkan baris MENTAH persis yang dikirim STM32 lewat serial
                # (termasuk baris non-JSON spt "[GPS]...", "[Jarak]...") — buat verifikasi
                # data asli dari mikrokontroler sebelum diolah.
                if LOG_RAW_SERIAL:
                    print(f"[STM32 raw] {raw_line}")

                # STM32 kadang kirim JSON dengan prefix teks (mis. "[->Raspi] {...}")
                # dan/atau ada garbage bytes nyempil. Ambil substring dari '{' pertama
                # sampai '}' terakhir, baru diparse — bukan cek startswith/endswith ketat.
                start_idx = raw_line.find("{")
                end_idx   = raw_line.rfind("}")
                if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
                    continue  # baris log biasa (mis. "[Jarak] ...", "[GPS] ..."), skip

                line = raw_line[start_idx:end_idx + 1]
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"[Dispatcher] JSON invalid: {e} | raw: {raw_line[:100]}")
                    continue

                # Metadata penelitian: seq (nomor urut → packet loss) + sentAt (jam
                # device → latency). STM32 EcoSort SUDAH sertakan seq/sentAt sendiri →
                # HORMATI punya device (jangan ditimpa). Hanya suntik kalau device
                # belum kirim (mis. firmware lama).
                if "seq" not in data:
                    _seq_counter += 1
                    data["seq"] = _seq_counter
                data.setdefault("sentAt", datetime.now(timezone.utc).isoformat())
                data.setdefault("nodeId", NODE_ID)
                # JSON yang diteruskan (tanpa field internal _*).
                fwd = json.dumps({k: v for k, v in data.items() if not str(k).startswith("_")})

                # 1. Update state in-memory (buat /sensor/latest)
                with sensor_lock:
                    latest_sensor = {**data, "_topic": TOPIC_SENSOR, "_timestamp": time.time()}

                # 2. Forward ke MQTT (→ backend VPS)
                if _mqtt_client is not None and mqtt_connected:
                    _mqtt_client.publish(TOPIC_SENSOR, fwd)

                # 3. Forward ke HTTP langsung ke server (transport=http) — non-blocking.
                if COMPARE_HTTP and _http_q is not None:
                    body = {k: v for k, v in data.items() if not str(k).startswith("_")}
                    body["transport"] = "http"
                    # packetLen = ukuran payload (byte). Backend pakai ini + latency
                    # buat hitung throughput HTTP.
                    body["packetLen"] = len(fwd.encode("utf-8"))
                    try:
                        _http_q.put_nowait(body)
                    except queue.Full:
                        pass  # backend lama down → buang, jangan sumbat baca STM32

                if LOG_SENSOR_EVERY > 0 and _seq_counter % LOG_SENSOR_EVERY == 0:
                    print(f"[STM32] seq={_seq_counter} | {_sensor_summary(data)}")
        except Exception as e:
            print(f"[Dispatcher] Error baca STM32: {e}")
            time.sleep(1)
        time.sleep(0.01)
    print("[Dispatcher] Thread berhenti.")


def start_dispatcher():
    global _dispatcher_thread
    _dispatcher_thread = threading.Thread(target=_serial_dispatcher_loop, daemon=True)
    _dispatcher_thread.start()

# ========================================================
# JALUR HTTP LANGSUNG KE SERVER (opsional)
# ========================================================
def _http_compare_worker():
    """POST tiap bacaan ke server (transport=http) di thread sendiri, biar loop
    baca STM32 tidak pernah berhenti nunggu jaringan."""
    while _is_running:
        try:
            body = _http_q.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            r = _http_session.post(INGEST_URL, json=body, timeout=10)
            if r.status_code not in (200, 201, 202):
                print(f"[CompareHTTP] seq={body.get('seq')} → HTTP {r.status_code}: {r.text[:80]}")
        except Exception as e:
            print(f"[CompareHTTP] seq={body.get('seq')} gagal: {e}")
        finally:
            _http_q.task_done()


def init_http_compare():
    """Siapkan session + antrean + worker untuk jalur HTTP. Hanya kalau COMPARE_HTTP=1."""
    global _http_q, _http_session
    if not COMPARE_HTTP:
        return
    try:
        import requests
    except ImportError:
        print("[CompareHTTP] modul 'requests' belum ada — jalur HTTP dimatikan. (pip install requests)")
        return
    _http_session = requests.Session()
    _http_session.headers.update({"Content-Type": "application/json"})
    if DEVICE_INGEST_KEY:
        _http_session.headers.update({"X-Device-Key": DEVICE_INGEST_KEY})
    else:
        print("[CompareHTTP] DEVICE_INGEST_KEY kosong — POST tanpa auth (hanya jalan kalau server juga tak set key).")
    _http_q = queue.Queue(maxsize=2000)
    threading.Thread(target=_http_compare_worker, daemon=True).start()
    print(f"[CompareHTTP] aktif → {INGEST_URL}")

# ========================================================
# MQTT
# ========================================================
def _on_connect(client, userdata, flags, rc, properties=None):
    global mqtt_connected
    if rc == 0:
        mqtt_connected = True
        client.subscribe(TOPIC_STATUS)  # sensor gak perlu di-subscribe balik, kita yg publish
        print(f"[MQTT] Connected!")
        client.publish(TOPIC_STATUS, json.dumps({"status": "online", "via": "fastapi"}), retain=True)
        remote.on_connect(client)   # subscribe topik perintah + publish state awal
    else:
        mqtt_connected = False
        print(f"[MQTT] Gagal connect rc={rc}")

def _on_disconnect(client, userdata, rc, properties=None, *args):
    global mqtt_connected
    mqtt_connected = False
    print(f"[MQTT] Disconnect rc={rc}, reconnect otomatis...")

def init_mqtt():
    global _mqtt_client
    try:
        client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2, client_id=f"fastapi-ecosort-{NODE_ID}")
    except AttributeError:
        client = mqtt_client.Client(client_id=f"fastapi-ecosort-{NODE_ID}")

    client.username_pw_set(MQTT_USER, MQTT_PASS)
    # Verifikasi TLS STANDAR OS (perbaikan tim IoT): HiveMQ Cloud kadang nolak
    # koneksi insecure (cert_reqs=CERT_NONE) → MQTT putus-nyambung / "sebagian jalan".
    # Butuh CA certs OS (Pi: `sudo apt install ca-certificates`).
    client.tls_set()
    client.on_connect    = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message    = remote.on_message

    # HARUS sebelum connect(): will_set hanya berlaku kalau didaftarkan
    # sebelum handshake CONNECT. Ini yang bikin status retained tidak
    # nyangkut "online" selamanya saat Pi mati mendadak.
    remote.attach(client)

    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()
        _mqtt_client = client
        print(f"[MQTT] Connecting ke {MQTT_HOST}:{MQTT_PORT}...")
    except Exception as e:
        print(f"[MQTT] Gagal init: {e}")

def publish_cmd(cmd: str) -> bool:
    if _mqtt_client is None or not mqtt_connected:
        print("[MQTT] Tidak terkoneksi, cmd dilewati.")
        return False
    try:
        _mqtt_client.publish(TOPIC_CMD, cmd)
        print(f"[MQTT] Publish cmd → {TOPIC_CMD}: {cmd}")
        return True
    except Exception as e:
        print(f"[MQTT] Gagal publish: {e}")
        return False

# ========================================================
# ARDUINO CMD
# ========================================================
ARDUINO_CMD = {"Organik": "organik", "Anorganik": "anorganik", "B3": "B3"}

# Perintah mentah yang dimengerti firmware STM32 (lihat prosesKategori() di .ino).
# "reset" tidak punya kategori — dia cuma mulangin piringan ke 0 derajat.
STM32_CMD_VALID = {"organik", "anorganik", "B3", "reset"}


def _ke_cmd_stm32(kategori: str):
    """Terima nama kategori ("Anorganik") maupun perintah mentah ("anorganik").

    Dulu cuma nerima kunci ARDUINO_CMD yang berhuruf besar. Akibatnya endpoint
    /arduino/ — yang ngirim nilai huruf kecil — SELALU gagal buat organik dan
    anorganik, lalu diam-diam nyasar ke MQTT dan tetap lapor sukses. Cuma "B3"
    yang kebetulan lolos karena dia sekaligus kunci dan nilai.
    """
    k = (kategori or "").strip()
    if k in STM32_CMD_VALID:
        return k
    return ARDUINO_CMD.get(k) or ARDUINO_CMD.get(k.capitalize())


def kirim_ke_stm32(kategori: str) -> dict:
    cmd = _ke_cmd_stm32(kategori)
    if not cmd:
        return {"ok": False, "channel": None, "reason": f"Kategori '{kategori}' tidak dikenal"}

    if arduino is not None and arduino.is_open:
        try:
            arduino.write((cmd + "\n").encode("utf-8"))
            arduino.flush()
            print(f"[Serial] Kirim ke STM32: {cmd}")
            return {"ok": True, "channel": "serial", "cmd": cmd}
        except Exception as e:
            print(f"[Serial] Gagal: {e}")

    if publish_cmd(cmd):
        return {"ok": True, "channel": "mqtt", "cmd": cmd}

    return {"ok": False, "channel": None, "reason": "Serial & MQTT keduanya tidak aktif"}

# Label model (Kapital) → enum backend SmartBIN (lowercase; "B3" → "b3").
BACKEND_LABEL_MAP = {"Organik": "organik", "Anorganik": "anorganik", "B3": "b3"}

def report_classification(kategori: str, confidence: float) -> bool:
    """Lapor hasil pemilahan ke backend SmartBIN lewat MQTT.

    Topik : smartbin/{NODE_ID}/classification
    Payload: {"label": <organik|anorganik|b3>, "confidence": <0..1>}
    Backend (src/mqtt/handlers/classificationData.js) menyimpan ke DB,
    memicu analitik + pairing berat/deposit, lalu broadcast ke frontend.
    """
    label = BACKEND_LABEL_MAP.get(kategori, "unknown")
    if _mqtt_client is None or not mqtt_connected:
        print("[MQTT] Tidak terkoneksi — hasil klasifikasi TIDAK dilaporkan ke backend.")
        return False
    payload = json.dumps({"label": label, "confidence": float(confidence)})
    try:
        _mqtt_client.publish(TOPIC_CLASSIFICATION, payload, qos=1)
        print(f"[MQTT] Lapor klasifikasi → {TOPIC_CLASSIFICATION}: {payload}")
        return True
    except Exception as e:
        print(f"[MQTT] Gagal lapor klasifikasi: {e}")
        return False

# ========================================================
# LOAD MODEL TFLITE
# ========================================================
interpreter    = None
input_details  = None
output_details = None
guard_interp   = None      # penjaga B3 (model lama) saat ensemble aktif
guard_in       = None
guard_out      = None
ENSEMBLE_ACTIVE = False
IN_H = IN_W   = 224
INPUT_DTYPE   = np.float32
_infer_lock    = threading.Lock()  # tflite tidak aman dipanggil paralel (kamera vs /predict/)


def _find_model(path):
    """Cari file model: apa adanya, atau satu tingkat di atas (kalau dijalankan
    dari backend/ tapi file ada di root repo). None kalau tak ketemu."""
    if os.path.exists(path):
        return path
    alt = os.path.join("..", path)
    return alt if os.path.exists(alt) else None


def _custom_layers(tf):
    """Layer custom milik model tim AI (model_sampah_Advanced = EfficientNet-B3 +
    channel attention). Tanpa ini load_model gagal: "Could not locate class
    'ChannelAttentionLayer'". Definisi disalin dari 'versi utk desktop/main.py:69'
    dan juga ada di sim_platform.py — kalau salah satu berubah, samain ketiganya,
    kalau tidak bobot ter-load ke arsitektur salah dan prediksi ngaco tanpa error."""
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
            return x * tf.reshape(avg_w + max_w, [-1, 1, 1, ch])

        def get_config(self):
            cfg = super().get_config()
            cfg.update({"reduction_ratio": self.reduction_ratio})
            return cfg

    return {"ChannelAttentionLayer": ChannelAttentionLayer}


class _KerasShim:
    """Bungkus model .keras biar antarmukanya sama persis dgn Interpreter tflite,
    supaya _run() / _preprocess() / jalur ensemble tidak perlu tahu bedanya.

    Di Raspi ini butuh TensorFlow penuh (tflite_runtime tidak bisa baca .keras) —
    berat di RAM dan lambat di-import. Dipakai untuk eksperimen; untuk produksi
    konversi dulu ke .tflite."""

    def __init__(self, path):
        import tensorflow as tf   # sengaja lokal: yang pakai tflite tidak kena beban
        self.model = tf.keras.models.load_model(
            path, compile=False, custom_objects=_custom_layers(tf))
        ish, osh = self.model.input_shape, self.model.output_shape
        self._in = [{"index": 0, "shape": [1, int(ish[1]), int(ish[2]), int(ish[3])],
                     "dtype": np.float32, "quantization": (0.0, 0)}]
        self._out = [{"index": 0, "shape": [1, int(osh[-1])],
                      "dtype": np.float32, "quantization": (0.0, 0)}]
        self._x = self._y = None

    def allocate_tensors(self):
        pass

    def get_input_details(self):
        return self._in

    def get_output_details(self):
        return self._out

    def set_tensor(self, index, value):
        self._x = value

    def invoke(self):
        self._y = self.model.predict(self._x, verbose=0)

    def get_tensor(self, index):
        return self._y


def _load_tflite(path):
    """Muat model .tflite ATAU .keras/.h5 → (interp, input_details, output_details)."""
    if path.lower().endswith((".keras", ".h5")):
        it = _KerasShim(path)
    else:
        it = tflite.Interpreter(model_path=path)
    it.allocate_tensors()
    return it, it.get_input_details(), it.get_output_details()


# Model utama: kalau ensemble aktif → model BARU; kalau file baru tak ada, jatuh ke
# model lama (tunggal) biar deploy tetap jalan walau model baru belum dicopy.
_primary_path = _find_model(MODEL_PATH) or MODEL_PATH
if ENSEMBLE:
    _new = _find_model(MODEL_PATH_NEW)
    if _new:
        _primary_path = _new
    else:
        print(f"[!] ENSEMBLE aktif tapi model baru '{MODEL_PATH_NEW}' tak ditemukan → pakai model tunggal (lama).")

MODEL_LOAD_ERROR = None   # diisi kalau model utama gagal dimuat (dipakai FE)
print(f"[+] Loading model TFLite utama dari: {_primary_path}")
try:
    interpreter, input_details, output_details = _load_tflite(_primary_path)
    shape       = input_details[0]['shape']
    IN_H, IN_W  = int(shape[1]), int(shape[2])
    INPUT_DTYPE = input_details[0]['dtype']
    print(f"[+] Model utama loaded! Input: {IN_W}x{IN_H}, dtype={INPUT_DTYPE.__name__}")
except Exception as e:
    interpreter = None
    # Simpan alasannya: tanpa ini FE cuma dapat "Model klasifikasi tidak ter-load"
    # dan penyebabnya (file tak ada / TF belum terpasang / OOM) cuma kelihatan di
    # journalctl. Lihat CameraWorker.start() dan /camera/status.
    MODEL_LOAD_ERROR = f"{type(e).__name__}: {e}"
    print(f"[!] Error loading model utama ({_primary_path}): {e}")

# Penjaga B3 = model lama. Dimuat hanya kalau ensemble aktif & utama = model baru.
if ENSEMBLE and interpreter is not None and _primary_path != (_find_model(MODEL_PATH) or MODEL_PATH):
    try:
        guard_interp, guard_in, guard_out = _load_tflite(_find_model(MODEL_PATH) or MODEL_PATH)
        ENSEMBLE_ACTIVE = True
        print(f"[+] Penjaga B3 loaded: {MODEL_PATH} | mode={ENSEMBLE_MODE}"
              + (f" (bobot penjaga {GUARD_WEIGHT:.2f})" if ENSEMBLE_MODE == "avg" else "")
              + f" | override B3 kalau prob B3 >= {B3_GATE*100:.0f}%"
              + (f", margin >= {B3_MARGIN*100:.0f}%" if B3_MARGIN > 0 else ", margin MATI")
              + (f", utama B3 >= {B3_PRIMARY_FLOOR*100:.0f}%" if B3_PRIMARY_FLOOR > 0 else ", veto MATI"))
    except Exception as e:
        print(f"[!] Gagal load penjaga B3 '{MODEL_PATH}': {e} → ensemble nonaktif, pakai model utama saja.")

def _preprocess(image: Image.Image) -> np.ndarray:
    image = image.convert("RGB").resize((IN_W, IN_H))
    arr   = np.array(image, dtype=np.float32)
    if INPUT_DTYPE == np.float32:
        if eff_preprocess is not None:
            # Desktop (TensorFlow penuh): fungsi resmi — untuk EfficientNet ini
            # pass-through (tidak mengubah nilai piksel).
            arr = eff_preprocess(arr)
        else:
            # Raspi (tflite-runtime tanpa TF): EfficientNet = pass-through, piksel
            # mentah 0-255. JANGAN normalisasi mean/std ImageNet (bikin prediksi ngaco).
            pass
        arr = np.expand_dims(arr, axis=0).astype(np.float32)
    else:
        arr = np.expand_dims(arr, axis=0).astype(INPUT_DTYPE)
    return arr

_B3_IDX = CLASS_NAMES.index("B3")


def _run(interp, ins, outs, inp):
    """Satu inferensi tflite → vektor probabilitas (sudah di-dequant kalau perlu)."""
    with _infer_lock:
        interp.set_tensor(ins[0]['index'], inp)
        interp.invoke()
        preds = interp.get_tensor(outs[0]['index'])[0].copy()
    if outs[0]['dtype'] != np.float32:
        scale, zero = outs[0]['quantization']
        if scale:
            preds = (preds.astype(np.float32) - zero) * scale
    return preds.astype(np.float32)


def _b3_should_override(g_preds, p_preds):
    """Putusin apakah hasil dipaksa jadi B3. Tiga syarat, semua harus lolos:
      (a) prob B3 guard >= B3_GATE
      (b) B3 guard menang >= B3_MARGIN dari kelas kedua-tertingginya
      (c) prob B3 model utama >= B3_PRIMARY_FLOOR  (veto: butuh suara kedua)
    Return (lolos, prob_b3_guard, daftar_alasan_gagal)."""
    g_b3 = float(g_preds[_B3_IDX])
    lain = [float(v) for i, v in enumerate(g_preds) if i != _B3_IDX]
    kedua = max(lain) if lain else 0.0
    p_b3 = float(p_preds[_B3_IDX])

    gagal = []
    if g_b3 < B3_GATE:
        gagal.append(f"guard B3 {g_b3*100:.0f}% < gate {B3_GATE*100:.0f}%")
    if B3_MARGIN > 0 and g_b3 - kedua < B3_MARGIN:
        gagal.append(f"margin {(g_b3 - kedua)*100:.0f}% < {B3_MARGIN*100:.0f}%")
    if B3_PRIMARY_FLOOR > 0 and p_b3 < B3_PRIMARY_FLOOR:
        gagal.append(f"utama B3 {p_b3*100:.0f}% < floor {B3_PRIMARY_FLOOR*100:.0f}% (VETO)")
    return (not gagal), g_b3, gagal


def _predict_vote(images):
    """Voting antar-frame: rata-ratain vektor probabilitas beberapa frame → 1
    keputusan. Lebih stabil daripada 1 frame (ngurangin loncat organik<->anorganik
    di objek susah kayak daun kering). 1 gambar = sama persis dengan single-frame."""
    imgs = images if isinstance(images, (list, tuple)) else [images]
    imgs = [im for im in imgs if im is not None]
    if not imgs:
        return None, 0.0

    acc = None            # jumlah probabilitas model utama
    g_acc = None          # jumlah probabilitas PENUH penjaga (margin butuh semua kelas)
    n = 0
    for image in imgs:
        inp   = _preprocess(image)
        preds = _run(interpreter, input_details, output_details, inp)
        acc   = preds if acc is None else acc + preds
        if ENSEMBLE_ACTIVE:
            g     = _run(guard_interp, guard_in, guard_out, inp)
            g_acc = g if g_acc is None else g_acc + g
        n += 1

    preds = acc / n                       # rata-rata probabilitas
    idx   = int(np.argmax(preds))
    label, conf = CLASS_NAMES[idx], float(preds[idx])

    # Gabungkan pendapat kedua model dulu (kalau modenya bukan "guard"), baru
    # gerbang B3 dijalankan di atas hasil gabungan itu.
    if ENSEMBLE_ACTIVE and ENSEMBLE_MODE in ("avg", "confident"):
        g_avg = g_acc / n
        if ENSEMBLE_MODE == "avg":
            w = max(0.0, min(1.0, GUARD_WEIGHT))
            gabung = preds * (1.0 - w) + g_avg * w
            asal = f"avg(w={w:.2f})"
        else:
            pakai_guard = float(g_avg.max()) > float(preds.max())
            gabung = g_avg if pakai_guard else preds
            asal = "penjaga" if pakai_guard else "utama"
        i2 = int(np.argmax(gabung))
        print(f"[ENS] {ENSEMBLE_MODE} → {CLASS_NAMES[i2]} {gabung[i2]*100:.0f}% via {asal} "
              f"[utama {label} {conf*100:.0f}% | penjaga {CLASS_NAMES[int(np.argmax(g_avg))]} "
              f"{float(g_avg.max())*100:.0f}%]")
        preds, idx = gabung, i2
        label, conf = CLASS_NAMES[idx], float(preds[idx])

    # Ensemble: penjaga B3 boleh maksa hasil jadi B3, tapi harus lolos gate +
    # margin + veto model utama. Rata-rata antar-frame dipakai di dua-duanya.
    if ENSEMBLE_ACTIVE:
        ok, g_b3, gagal = _b3_should_override(g_acc / n, preds)
        if ok:
            print(f"[ENS] gerbang B3 → guard B3={g_b3*100:.0f}% "
                  f"(utama bilang {label} {conf*100:.0f}%)")
            return "B3", g_b3
        print(f"[ENS] ikut utama → {label} {conf*100:.0f}%  "
              f"[guard B3={g_b3*100:.0f}% ditolak: " + "; ".join(gagal) + "]")
    return label, conf


def _predict(image: Image.Image):
    """Single-frame (dipakai endpoint HTTP /predict). Loop kamera pakai voting."""
    return _predict_vote([image])

# ========================================================
# KAMERA REALTIME DI RASPI (motion-gate + auto klasifikasi)
# ========================================================
# Saat main.py jalan, kamera Raspi langsung dibuka (AUTO_START_CAM=1 default).
# TANPA buka web: begitu ada objek masuk (gerakan terdeteksi) → tunggu diam →
# jepret → klasifikasi → aktuasi STM32 → lapor ke backend SmartBIN.
try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    cv2 = None
    _HAS_CV2 = False
    print("[!] OpenCV (cv2) belum terpasang. Kamera realtime nonaktif. -> pip install opencv-python")

# Semua bisa di-override lewat env var (tanpa ubah kode)
CAMERA_INDEX     = int(os.environ.get("CAMERA_INDEX", "0"))           # 0 = kamera default Raspi
MOTION_THRESHOLD = int(os.environ.get("MOTION_THRESHOLD", "1500000")) # total piksel berubah utk dianggap "ada objek"
SETTLE_DELAY     = float(os.environ.get("SETTLE_DELAY", "0.6"))       # detik tunggu objek diam sebelum jepret
CONF_THRESHOLD   = float(os.environ.get("CONF_THRESHOLD", "0"))       # 0 = TANPA batas: tiap objek langsung diproses apapun confidence-nya
COOLDOWN_SEC     = float(os.environ.get("COOLDOWN_SEC", "3.0"))       # jeda saat TAK ada aktuasi (conf rendah / jepret gagal)
# Debug: simpan frame yang PERSIS diklasifikasi ke file, biar keliatan "AI liat apa"
# (bandingin blur/pencahayaan/background vs shot bersih di browser). SAVE_SHOT=0 matiin.
SAVE_SHOT = os.environ.get("SAVE_SHOT", "1") == "1"
SHOT_PATH = os.environ.get("SHOT_PATH", "last_shot.jpg")

# Mode KUMPULIN DATASET (buat retrain model): tiap objek yang di-capture disimpan ke
# dataset/<DATASET_LABEL>/<timestamp>.jpg. Set DATASET_LABEL = jenis yang lagi ditaruh
# (organik/anorganik/b3) biar langsung ter-label benar. Ganti label → restart.
#   DATASET_CAPTURE=1 DATASET_LABEL=anorganik python3 main.py
DATASET_CAPTURE = os.environ.get("DATASET_CAPTURE", "0") == "1"
DATASET_DIR     = os.environ.get("DATASET_DIR", "dataset")
DATASET_LABEL   = os.environ.get("DATASET_LABEL", "unsorted")

# Deteksi objek DI ATAS platform (buletan merah): fokus ROI tengah + bandingkan dgn
# kondisi KOSONG (baseline). Cuma analisis kalau ada objek nutupin platform & sudah
# diam, SEKALI per objek. Nilai = rata-rata beda piksel (0-255). Sesuaikan via env.
CAMERA_WARMUP_SEC = float(os.environ.get("CAMERA_WARMUP_SEC", "2.0"))  # tunggu kamera settle (auto-exposure) sebelum ambil baseline → cegah false-trigger di awal
# Gerbang STM32: pemilahan ditahan sampai board terbukti hidup (ada paket masuk).
# STM32_SIAP_SEC harus DI ATAS siklus kirim alami board. Diukur 2026-09-02:
# bin-003 mengirim tiap ~33 detik, sangat konsisten (32,7 / 32,8 / 33,0).
# Angka di bawah 33 bikin board sehat dianggap mati — itu yang sempat terjadi
# waktu ambang watchdog diset 30 detik dan board yang normal malah di-reset terus.
TUNGGU_STM32   = os.environ.get("TUNGGU_STM32", "1") not in ("0", "false", "False", "")
STM32_SIAP_SEC = float(os.environ.get("STM32_SIAP_SEC", "90"))

IDLE_LOG_SEC = float(os.environ.get("IDLE_LOG_SEC", "15"))  # interval log "menunggu objek" saat platform kosong (biar keliatan hidup, tak spam)
ROI_FRAC    = float(os.environ.get("ROI_FRAC", "0.6"))    # fraksi tengah frame (area buletan) yg dipantau+diklasifikasi
# Input model = crop ROI, BUKAN frame penuh. Di rig Pi frame penuh didominasi
# platform MERAH + bodi HIJAU, objeknya cuma sebagian kecil — model jadi mutusin
# berdasarkan warna alas, bukan sampahnya (daun ke-vote Anorganik 96%/B3 73%).
# Di sim laptop objek dipegang & memenuhi frame, makanya di sana kelihatan benar.
# CLASSIFY_ROI=0 buat balik ke perilaku lama (frame penuh).
CLASSIFY_ROI = os.environ.get("CLASSIFY_ROI", "1") not in ("0", "false", "False", "")
# Geser titik pusat ROI kalau kamera tidak pas lurus di atas buletan. Fraksi dari
# LEBAR/TINGGI frame, + = kanan/bawah. Cara nyari nilainya: buka feed kamera di
# dashboard, lihat kotak "area deteksi" — kalau buletan melenceng ke kanan 12%
# lebar frame, set ROI_DX=0.12. Default 0 = persis tengah (perilaku lama).
ROI_DX      = float(os.environ.get("ROI_DX", "0.0"))
ROI_DY      = float(os.environ.get("ROI_DY", "0.0"))
# Bandingkan WARNA (BGR, ambil selisih kanal terbesar), bukan grayscale. Alas merah
# terang dan daun gelap kehijauan punya luminansi nyaris sama (158.4 vs 156.6) —
# di grayscale daunnya tak terlihat sama sekali, padahal beda 39 di kanal merah.
# DIFF_COLOR=0 balik ke grayscale (perilaku lama, lebih hemat CPU sedikit).
DIFF_COLOR = os.environ.get("DIFF_COLOR", "1") not in ("0", "false", "False", "")
# Metrik warna nilainya ~1.75x grayscale (diukur dari frame rig). Ambang lama
# dikalibrasi buat grayscale, jadi kalau dipakai apa adanya: OBJECT_DIFF jadi
# kelewat gampang (false trigger) DAN CLEAR_DIFF jadi kelewat ketat — platform
# kosong tak pernah dianggap kosong, sistem tak pernah re-arm setelah aktuasi.
# Default di bawah ikut skala; override lewat env tetap dipakai apa adanya.
_DSKALA = 1.75 if DIFF_COLOR else 1.0
OBJECT_DIFF = float(os.environ.get("OBJECT_DIFF", 18 * _DSKALA))  # beda dari kosong utk dianggap ADA objek
# obj_diff (rata-rata beda piksel) SENDIRIAN gampang ketipu: cahaya ruangan geser
# atau auto-exposure kamera nyetel ulang bikin SELURUH ROI berubah tipis, rata-rata
# naik lewat OBJECT_DIFF padahal buletan kosong → deteksi hantu. Objek beneran beda:
# dia bikin sebagian piksel berubah TAJAM, bukan semua piksel berubah dikit.
OBJECT_PIXEL_DELTA = float(os.environ.get("OBJECT_PIXEL_DELTA", "30"))  # beda per-piksel yg dihitung "berubah tajam"
OBJECT_AREA_MIN    = float(os.environ.get("OBJECT_AREA_MIN", "0.04"))   # min fraksi ROI yg berubah tajam (0.04 = 4%)
# Baseline ikut hanyut pelan selama platform kosong, biar drift cahaya sepanjang
# hari tidak numpuk jadi false-trigger. 0 = matikan (baseline beku, perilaku lama).
BASELINE_ALPHA     = float(os.environ.get("BASELINE_ALPHA", "0.02"))
CLEAR_DIFF  = float(os.environ.get("CLEAR_DIFF", 9 * _DSKALA))   # beda di bawah ini = platform kosong lagi
STILL_MOVE  = float(os.environ.get("STILL_MOVE", 4 * _DSKALA))   # gerak antar-frame di bawah ini = objek diam
# Syarat UTAMA "platform sudah kosong" buat re-arm: fraksi piksel yang berubah tajam.
# Pakai AREA, bukan rata-rata: platform ini berputar, jadi setelah aktuasi lubang
# baut & goresan berhenti di posisi lain — tampilannya tak pernah balik PERSIS ke
# baseline lama walau buletannya benar-benar kosong. Perbedaan kecil tersebar itu
# ngangkat obj_diff tapi hampir tak nambah AREA. Objek nyata bikin blob besar.
CLEAR_AREA  = float(os.environ.get("CLEAR_AREA", "0.02"))        # < 2% area berubah = kosong
STILL_NEED  = int(os.environ.get("STILL_NEED", "3"))      # butuh N frame diam berturut sebelum jepret
REARM_BUFFER = float(os.environ.get("REARM_BUFFER", "1.5"))  # jeda ekstra setelah aktuator selesai sebelum siap objek baru
REARM_STILL_NEED = int(os.environ.get("REARM_STILL_NEED", "8"))  # frame DIAM berturut yg wajib sebelum baseline baru diambil
# Batas nunggu platform balik kosong sebelum baseline DIPAKSA diperbarui. Ini katup
# pengaman biar tidak deadlock kalau kamera kesenggol / cahaya berubah drastis —
# TAPI kalau yang bikin beda itu objek yang masih nangkring, objek tsb ikut ke-serap
# jadi "kondisi kosong" dan tidak akan terdeteksi lagi. Makanya sengaja lama, dan
# selama nunggu tetap ngeluarin peringatan. 0 = nunggu selamanya (tidak pernah dipaksa).
REARM_MAX_WAIT   = float(os.environ.get("REARM_MAX_WAIT", "60"))
REARM_WARN_SEC   = float(os.environ.get("REARM_WARN_SEC", "10"))  # interval peringatan saat nunggu
# Fase GRACE setelah mekanik berhenti, sebelum sistem benar-benar armed. Diamati di
# rig: baseline diambil saat scene sudah diam, lalu alas masih bergeser sedikit ke
# posisi istirahat final dan BERHENTI STABIL di situ. Scene diam (lolos STILL_NEED
# berapa pun) tapi beda jauh dari baseline → jepret platform kosong. Tidak ada
# ambang yang bisa menangkal ini karena masalahnya KAPAN baseline diambil.
# Selama grace, baseline terus disamakan dengan frame sekarang supaya pergeseran
# sisa terserap. Objek yang ditaruh saat grace TIDAK ikut terserap: penyegaran
# baseline berhenti begitu perubahannya sebesar objek (>= CLEAR_AREA).
REARM_GRACE_SEC  = float(os.environ.get("REARM_GRACE_SEC", "2.0"))


def _center_roi(frame, frac, dx=None, dy=None):
    """Crop kotak ROI (area platform/buletan). frac=0.6 → sisi 60% dari sisi
    terpendek frame. dx/dy menggeser PUSAT crop (fraksi lebar/tinggi frame,
    + = kanan/bawah) buat kamera yang tidak lurus di atas buletan; kalau None
    pakai ROI_DX/ROI_DY. Kotak selalu dijaga tetap di dalam frame."""
    h, w = frame.shape[:2]
    s = int(min(h, w) * max(0.1, min(1.0, frac)))
    dx = ROI_DX if dx is None else dx
    dy = ROI_DY if dy is None else dy
    x0 = int(w / 2 + dx * w) - s // 2
    y0 = int(h / 2 + dy * h) - s // 2
    x0 = max(0, min(w - s, x0))   # clamp: jangan keluar frame
    y0 = max(0, min(h - s, y0))
    return frame[y0:y0 + s, x0:x0 + s]

# Mode OBJEK (motion-gate 1x): pas objek masuk → jepret+analisis SEKALI, lalu tunggu
# objek diangkat (scene sepi >= REARM_CLEAR_SEC) baru siap objek berikutnya. Cegah
# analisis berulang objek yang sama & spam confidence-rendah.
REARM_CLEAR_SEC  = float(os.environ.get("REARM_CLEAR_SEC", "2.0"))
AUTO_START_CAM   = os.environ.get("AUTO_START_CAM", "1") == "1"       # default ON di Raspi (set 0 utk matikan)

# Monitor kamera di dashboard: push frame TERAKHIR ke backend tiap CAMERA_PUSH_SEC
# (Pi konek KELUAR ke server publik — tak perlu buka port Pi). Backend simpan frame
# terakhir per bin; FE ambil via <img> /camera/{nodeId}/latest.jpg.
CAMERA_PUSH     = os.environ.get("CAMERA_PUSH", "1") == "1"
CAMERA_PUSH_SEC = float(os.environ.get("CAMERA_PUSH_SEC", "1.5"))
CAMERA_JPEG_Q   = int(os.environ.get("CAMERA_JPEG_Q", "70"))

# Durasi aktuator per kategori (detik). Setelah aktuasi, kamera DIKUNCI selama ini
# supaya tidak jepret ulang saat mekanik (stepper+tilt+servo+auto-reset) MASIH GERAK
# — mencegah deteksi/aktuasi dobel & frame ngaco. STM32 TIDAK diubah; timing di sisi
# Raspi. Ukur ulang sekali dgn stopwatch di hardware asli, tambah ~1s buffer, lalu
# override per-kategori lewat env: ACTUATOR_SEC_ORGANIK / _ANORGANIK / _B3.
ACTUATOR_TIMES = {
    "organik":   float(os.environ.get("ACTUATOR_SEC_ORGANIK",   "7.0")),
    "anorganik": float(os.environ.get("ACTUATOR_SEC_ANORGANIK", "8.0")),
    "b3":        float(os.environ.get("ACTUATOR_SEC_B3",         "9.5")),
}
ACTUATOR_DEFAULT_SEC = float(os.environ.get("ACTUATOR_SEC_DEFAULT", "10.0"))

def _actuator_lock_sec(kategori: str) -> float:
    """Estimasi durasi aktuator utk kategori (case-insensitive: 'Organik'→'organik')."""
    return ACTUATOR_TIMES.get((kategori or "").lower().strip(), ACTUATOR_DEFAULT_SEC)


class CameraWorker:
    """Loop kamera di thread terpisah supaya endpoint HTTP tetap responsif."""

    def __init__(self):
        self.thread  = None
        self.running = False
        self.cap     = None
        self.last    = {"kategori": None, "confidence": None, "ts": None}
        self.last_raw = None   # frame BGR terakhir (buat push monitor ke dashboard)
        # ROI + peta selisih terakhir — dipakai /camera/snapshot biar bisa LIHAT
        # apa yang dianggap "objek", bukan cuma nebak dari angka obj_diff.
        self.last_roi  = None
        self.last_diff = None
        self.error   = None
        # Kondisi detektor SAAT INI — dibaca /camera/status. Tanpa ini satu-satunya
        # cara tau kenapa objek tak ke-trigger adalah baca journalctl di Pi.
        self.debug   = {}

    def start(self):
        if not _HAS_CV2:
            self.error = "OpenCV belum terpasang (pip install opencv-python)"
            print(f"[CAM] {self.error}")
            return False
        if interpreter is None:
            self.error = ("Model klasifikasi tidak ter-load"
                          + (f" — {MODEL_LOAD_ERROR}" if MODEL_LOAD_ERROR else "")
                          + f" (MODEL_PATH={MODEL_PATH}, ENSEMBLE={'1' if ENSEMBLE else '0'})")
            print(f"[CAM] {self.error}")
            return False
        if self.running:
            return True
        self.error   = None
        self.running = True
        self.thread  = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None
        if self.cap:
            self.cap.release()
            self.cap = None

    def _loop(self):
        self.cap = cv2.VideoCapture(CAMERA_INDEX)
        if not self.cap or not self.cap.isOpened():
            self.error = f"Kamera index {CAMERA_INDEX} gagal dibuka"
            print(f"[CAM] {self.error}")
            self.running = False
            return

        print(f"[CAM] Kamera AKTIF (index {CAMERA_INDEX}) — mode PLATFORM: analisis objek di ROI tengah (buletan), 1x per objek.")
        print("[CAM] (pastikan platform/buletan KOSONG saat start — dipakai sbg baseline)")
        baseline = None       # ROI abu-abu saat platform KOSONG
        prev_roi = None       # ROI frame sebelumnya (deteksi gerak)
        armed = True
        still = 0
        settle = 0            # frame diam berturut saat nunggu mekanik berhenti (re-arm)
        warn_at = 0.0         # waktu peringatan "platform belum kosong" berikutnya
        grace_until = 0.0     # akhir fase grace (baseline masih dilaraskan, belum armed)
        rearm_at = 0.0        # waktu boleh re-arm (setelah aktuator selesai)
        warmup_until = time.time() + CAMERA_WARMUP_SEC  # settle auto-exposure dulu
        n_obj = 0             # nomor urut objek yang sudah diproses
        detecting = False     # objek lagi diamati (buat log "objek masuk" sekali)
        idle_log_at = 0.0     # waktu berikutnya log "menunggu objek"
        warmed = False        # sudah lewat warmup (buat log sekali)

        while self.running:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            self.last_raw = frame  # frame penuh buat feed monitor

            # Warmup: kamera settle dulu sebelum ambil baseline & mulai deteksi,
            # supaya frame gelap/nyetel di awal tak dikira "objek" → gerak sendiri.
            if time.time() < warmup_until:
                if not warmed:
                    print(f"[CAM] ⏳ Warmup {CAMERA_WARMUP_SEC:.0f}s — kamera menyala & settle (pastikan buletan KOSONG)...")
                    warmed = True
                time.sleep(0.03)
                continue

            roi = _center_roi(frame, ROI_FRAC)
            # vis = citra yang dipakai buat DETEKSI (bukan input model). Berwarna
            # kalau DIFF_COLOR, kalau tidak grayscale seperti versi lama.
            vis = cv2.GaussianBlur(roi if DIFF_COLOR
                                   else cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), (21, 21), 0)

            if baseline is None:
                baseline = vis.astype(np.float32)   # anggap platform kosong saat start
                prev_roi = vis
                idle_log_at = time.time() + IDLE_LOG_SEC
                print("[CAM] ✅ Baseline platform KOSONG diambil — SIAP, menunggu objek di buletan...")
                time.sleep(0.03)
                continue

            grayf = vis.astype(np.float32)
            # Selisih per-piksel = kanal yang paling beda (bukan rata-rata kanal):
            # objek yang cuma beda di satu kanal — persis kasus daun vs alas merah —
            # tetap kebaca penuh, tidak ke-encerin kanal lain yang kebetulan mirip.
            _d = cv2.absdiff(grayf, baseline)
            diff_map = _d.max(axis=2) if _d.ndim == 3 else _d       # beda dari kondisi kosong
            obj_diff = float(diff_map.mean())                       # rata-rata (kena drift cahaya)
            obj_area = float((diff_map > OBJECT_PIXEL_DELTA).mean())  # fraksi piksel berubah TAJAM
            self.last_roi, self.last_diff = roi, diff_map
            _m = cv2.absdiff(vis, prev_roi)
            move     = float((_m.max(axis=2) if _m.ndim == 3 else _m).mean())  # gerak antar-frame
            prev_roi = vis

            # --- GERBANG STM32 ---
            # Pemilahan tidak boleh jalan sebelum STM32 terbukti hidup. Kalau
            # kamera memilah duluan, hasilnya: klasifikasi benar, perintah ditulis
            # ke port, "[Serial] Kirim ke STM32" tampil sukses — tapi tidak ada
            # yang bergerak, karena board belum siap. Objeknya sudah telanjur
            # dianggap selesai dan tidak akan diproses ulang.
            # Bukti hidup = ada paket masuk, bukan serial_stm32 "connected"
            # (port kebuka di OS tetap "connected" walau board diam).
            umur_stm = umur_data_stm32()
            stm32_siap = (not TUNGGU_STM32) or (umur_stm < STM32_SIAP_SEC)
            # inf tidak valid di JSON → None = "belum pernah ada data sama sekali"
            umur_stm_json = None if umur_stm == float("inf") else round(umur_stm, 1)

            self.debug = {
                "stm32_siap": stm32_siap,
                "stm32_umur_data": umur_stm_json,
                "armed": armed,                       # False = lagi nunggu platform kosong
                "obj_diff": round(obj_diff, 1),       # vs ambang OBJECT_DIFF
                "obj_area_pct": round(obj_area * 100, 2),   # vs OBJECT_AREA_MIN / CLEAR_AREA
                "move": round(move, 1),               # vs STILL_MOVE
                "still": still,                       # butuh STILL_NEED buat jepret
                "settle": settle,                     # butuh REARM_STILL_NEED buat re-arm
                "detik_nunggu_rearm": (round(time.time() - rearm_at, 1)
                                       if not armed and rearm_at else None),
                "objek_ke": n_obj,
                # Kesimpulan siap-pakai: kenapa frame ini tidak nge-trigger.
                "kenapa": (
                    ("nunggu STM32 siap (belum pernah ada data masuk)"
                     if umur_stm_json is None else
                     f"nunggu STM32 siap (data terakhir {umur_stm:.0f}s lalu)")
                    if not stm32_siap else
                    "lagi nunggu platform kosong (armed=False)" if not armed else
                    f"obj_diff {obj_diff:.1f} <= OBJECT_DIFF {OBJECT_DIFF:.1f}"
                    if obj_diff <= OBJECT_DIFF else
                    f"area {obj_area*100:.2f}% <= OBJECT_AREA_MIN {OBJECT_AREA_MIN*100:.1f}%"
                    if obj_area <= OBJECT_AREA_MIN else
                    f"masih gerak: move {move:.1f} >= STILL_MOVE {STILL_MOVE:.1f}"
                    if move >= STILL_MOVE else
                    f"nunggu diam: still {still}/{STILL_NEED}"
                ),
            }

            if not stm32_siap:
                # Baseline tetap digeser pelan biar tidak basi selama nunggu, tapi
                # TIDAK ada deteksi/aktuasi sampai STM32 kasih kabar hidup.
                if BASELINE_ALPHA > 0 and obj_area < CLEAR_AREA:
                    cv2.accumulateWeighted(grayf, baseline, BASELINE_ALPHA)
                detecting = False
                still = 0
                if time.time() >= idle_log_at:
                    kabar = ("belum pernah ada data masuk sama sekali"
                             if umur_stm_json is None else
                             f"data terakhir {umur_stm:.0f}s lalu (batas {STM32_SIAP_SEC:.0f}s)")
                    print(f"[CAM] ⏸ nunggu STM32 siap — {kabar}. Pemilahan ditahan.")
                    idle_log_at = time.time() + IDLE_LOG_SEC
            elif armed:
                # Objek nutupin platform (beda dari kosong) DAN sudah diam beberapa frame.
                if obj_diff > OBJECT_DIFF and obj_area > OBJECT_AREA_MIN and move < STILL_MOVE:
                    if not detecting:
                        detecting = True
                        print(f"[CAM] 👀 Objek MASUK buletan (obj_diff={obj_diff:.0f} > {OBJECT_DIFF:.0f}, "
                              f"area={obj_area*100:.1f}% > {OBJECT_AREA_MIN*100:.1f}%) — tunggu diam...")
                    still += 1
                    if still >= STILL_NEED:
                        detecting = False
                        shot = frame
                        vote_shots = []             # kumpulan frame untuk voting
                        for _ in range(VOTE_FRAMES):
                            ok2, f2 = self.cap.read()   # ambil frame segar
                            if ok2:
                                shot = f2
                            vote_shots.append(shot)
                            if VOTE_DELAY > 0:
                                time.sleep(VOTE_DELAY)
                        self.last_raw = shot
                        if SAVE_SHOT:
                            try:
                                cv2.imwrite(SHOT_PATH, shot)  # frame penuh yang diklasifikasi
                            except Exception:
                                pass
                        if DATASET_CAPTURE:
                            try:
                                ddir = os.path.join(DATASET_DIR, DATASET_LABEL)
                                os.makedirs(ddir, exist_ok=True)
                                fn = os.path.join(ddir, f"{int(time.time() * 1000)}.jpg")
                                cv2.imwrite(fn, shot)
                                print(f"[Dataset] +1 ({DATASET_LABEL}) → {fn}")
                            except Exception as e:
                                print(f"[Dataset] gagal simpan: {e}")
                        # Input model = crop ROI (area buletan), biar warna platform
                        # merah/hijau di pinggir tidak ikut menentukan kelas. Samain
                        # dengan sim_platform.py --live yang selama ini hasilnya benar.
                        def _siap(fr):
                            src = _center_roi(fr, ROI_FRAC) if CLASSIFY_ROI else fr
                            return Image.fromarray(cv2.cvtColor(src, cv2.COLOR_BGR2RGB))

                        img = _siap(shot)
                        try:
                            vote_imgs = [_siap(s) for s in vote_shots] or [img]
                            kategori, conf = _predict_vote(vote_imgs)
                        except Exception as e:
                            print(f"[CAM] gagal klasifikasi: {e}")
                            kategori, conf = None, 0.0

                        n_obj += 1
                        teraktuasi = bool(kategori) and conf >= CONF_THRESHOLD
                        if teraktuasi:
                            print(f"[CAM] #{n_obj} ✓ {kategori} ({conf:.0%}) → aktuasi STM32 + lapor backend")
                            kirim_ke_stm32(kategori)
                            report_classification(kategori, conf)
                            self.last = {"kategori": kategori, "confidence": conf, "ts": time.time()}
                        else:
                            print(f"[CAM] #{n_obj} objek di platform, confidence rendah ({conf:.0%}) — dilewati")

                        armed = False
                        still = 0
                        if teraktuasi:
                            # Aktuator jalan → objek mestinya jatuh & mekanik reset.
                            wait_s = _actuator_lock_sec(kategori) + REARM_BUFFER
                            print(f"[CAM] ⏳ tunggu aktuator ~{wait_s:.1f}s (objek jatuh + mekanik reset)...")
                        else:
                            # TIDAK ada aktuasi → objek MASIH di platform. Jangan pakai
                            # timer aktuator (tidak ada yang jalan); cukup jeda pendek,
                            # lalu syarat CLEAR_DIFF di bawah yang nunggu objek diangkat.
                            wait_s = COOLDOWN_SEC
                            print(f"[CAM] ⏳ tidak diaktuasi — objek masih di platform, "
                                  f"angkat dulu (cek lagi tiap {wait_s:.1f}s)...")
                        rearm_at = time.time() + wait_s
                else:
                    if detecting:
                        # objek keburu pindah/goyang sebelum diam → batal, tunggu lagi
                        detecting = False
                        print(f"[CAM] objek belum diam / pindah (obj_diff={obj_diff:.0f}, area={obj_area*100:.1f}%) — tunggu lagi...")
                    still = 0
                    # Platform kosong & tenang → geser baseline pelan ngikutin cahaya
                    # sekarang. Ini yang bikin drift lampu/auto-exposure tidak numpuk.
                    if BASELINE_ALPHA > 0 and obj_area < CLEAR_AREA:
                        cv2.accumulateWeighted(grayf, baseline, BASELINE_ALPHA)
                    # Heartbeat: platform kosong & siap. Log tiap IDLE_LOG_SEC biar
                    # keliatan sistem hidup tanpa spam tiap frame.
                    if time.time() >= idle_log_at:
                        print(f"[CAM] … menunggu objek (platform kosong, obj_diff={obj_diff:.0f}, area={obj_area*100:.1f}%)")
                        idle_log_at = time.time() + IDLE_LOG_SEC
            else:
                # Re-arm: timer aktuator habis SAJA tidak cukup. Durasi ACTUATOR_TIMES
                # cuma perkiraan — mekanik (tilt/servo/auto-reset) bisa MASIH gerak pas
                # timer bunyi. Kalau baseline diambil saat itu, isinya "platform miring /
                # objek nyangkut"; begitu platform balik ke posisi rest, obj_diff vs
                # baseline jelek itu meledak > OBJECT_DIFF padahal buletan KOSONG →
                # false-trigger → yang diklasifikasi PLATFORM KOSONG, dan platform kosong
                # selalu jatuh ke kelas yang sama tiap siklus.
                # Makanya: tunggu scene benar-benar DIAM dulu, baru ambil baseline.
                if grace_until:
                    # Laraskan baseline ke kondisi sekarang selama grace — kecuali
                    # perubahannya sebesar objek, yang berarti ada yang ditaruh dan
                    # tidak boleh ikut jadi "kondisi kosong".
                    if obj_area < CLEAR_AREA:
                        baseline = grayf.copy()
                    if time.time() >= grace_until:
                        grace_until = 0.0
                        armed = True
                        settle = 0
                        warn_at = 0.0
                        idle_log_at = time.time() + IDLE_LOG_SEC
                        print(f"[CAM] ✅ SIAP objek berikutnya (#{n_obj + 1}) — grace "
                              f"{REARM_GRACE_SEC:.1f}s selesai, obj_diff={obj_diff:.0f}.")
                elif time.time() >= rearm_at:
                    # "Diam" saja tidak cukup — mekanik bisa berhenti sesaat di tengah
                    # jalan (mis. puncak tilt) dan itu lolos syarat diam dalam 0,3 dtk.
                    # Wajib juga MIRIP kondisi kosong yang lama (obj_diff < CLEAR_DIFF),
                    # jadi baseline tidak pernah keambil pas platform masih miring.
                    diam  = move < STILL_MOVE
                    # CUKUP SALAH SATU sinyal bilang bersih. Diukur dari rig: habis
                    # aktuasi buletan kosong tapi alas berhenti di rotasi lain →
                    # area 7-8% (lubang baut pindah) padahal obj_diff cuma 8-15.
                    # Objek yang benar-benar nangkring: area 54%, obj_diff 50.
                    # Pakai AND bikin kasus pertama tak pernah lolos → nunggu 60s
                    # tiap siklus, dan objek yang ditaruh selama itu terabaikan.
                    pulih = obj_area < CLEAR_AREA or obj_diff < CLEAR_DIFF
                    settle = settle + 1 if (diam and pulih) else 0
                    telat = REARM_MAX_WAIT > 0 and time.time() >= rearm_at + REARM_MAX_WAIT
                    if not pulih and time.time() >= warn_at:
                        # Kasih tau SELAMA nunggu, bukan cuma pas nyerah — biar kelihatan
                        # bedanya "lagi nunggu diangkat" vs "sistem nge-hang".
                        print(f"[CAM] ⏸ platform belum kosong (area={obj_area*100:.1f}% > "
                              f"{CLEAR_AREA*100:.1f}% DAN obj_diff={obj_diff:.0f} >= "
                              f"{CLEAR_DIFF:.0f}) — angkat objeknya biar siap lagi.")
                        warn_at = time.time() + REARM_WARN_SEC
                    if settle >= REARM_STILL_NEED or (telat and diam):
                        if settle < REARM_STILL_NEED:
                            # Katup pengaman. Efek sampingnya nyata: apa pun yang masih
                            # ada di platform sekarang jadi bagian dari "kosong".
                            print(f"[CAM] ⚠️ {REARM_MAX_WAIT:.0f}s platform tak balik ke kondisi kosong "
                                  f"(area={obj_area*100:.1f}% > {CLEAR_AREA*100:.1f}%) — baseline DIPAKSA "
                                  f"diperbarui. Kalau ada objek yang masih nangkring, mulai sekarang "
                                  f"dia dianggap bagian dari platform & tak akan terdeteksi. "
                                  f"Cek objek nyangkut / kamera bergeser.")
                        settle = 0
                        warn_at = 0.0
                        baseline = grayf.copy()
                        grace_until = time.time() + REARM_GRACE_SEC
                        print(f"[CAM] platform diam & kosong (obj_diff={obj_diff:.0f}) — "
                              f"grace {REARM_GRACE_SEC:.1f}s biar mekanik benar-benar mapan...")

            time.sleep(0.03)  # ~30fps buat feed, hemat CPU

        if self.cap:
            self.cap.release()
            self.cap = None
        print("[CAM] Kamera realtime berhenti.")


camera_worker = CameraWorker()


def _camera_push_loop():
    """Push frame kamera TERAKHIR ke backend tiap CAMERA_PUSH_SEC untuk MONITOR
    di dashboard. Encode JPEG di sini (bukan tiap frame) supaya hemat CPU. Pi
    konek KELUAR ke server (BACKEND_HTTP_URL) — tak perlu buka port di Pi."""
    try:
        import requests
    except ImportError:
        print("[CamPush] modul 'requests' belum ada — monitor kamera nonaktif.")
        return
    url = f"{BACKEND_HTTP_URL}/camera/frame"
    while _is_running:
        time.sleep(CAMERA_PUSH_SEC)
        if not (CAMERA_PUSH and camera_worker.running):
            continue
        frame = camera_worker.last_raw
        if frame is None or not _HAS_CV2:
            continue
        try:
            ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, CAMERA_JPEG_Q])
            if not ok:
                continue
            requests.post(url, data=jpg.tobytes(), timeout=5, headers={
                "Content-Type": "image/jpeg",
                "X-Node-Id": NODE_ID,
                "X-Device-Key": DEVICE_INGEST_KEY,
            })
        except Exception as e:
            print(f"[CamPush] gagal kirim frame: {e}")

# ========================================================
# GEMINI
# ========================================================
STATIC_TIPS = {
    "Anorganik": "• Pisahkan dari organik.\n• Jangan dibakar.\n• Bawa ke bank sampah.\n• Cuci bersih kemasan.",
    "B3": "• JANGAN buang ke tempat biasa.\n• Kumpulkan di drop-box B3.\n• Baterai dan lampu termasuk B3.",
    "Organik": "• Bisa dijadikan kompos.\n• Jangan campur plastik.\n• Cocok untuk eco-enzyme.",
}

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
gemini_client  = None
tips_cache: dict = {}

if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    print("[+] Gemini siap!")
else:
    print("[!] GEMINI_API_KEY tidak ditemukan. (Tips akan menggunakan mode statis)")

# ========================================================
# FASTAPI LIFESPAN
# ========================================================

def _register_remote_actions():
    """Sambungkan perintah MQTT ke fungsi yang SUDAH ada di file ini.

    Tidak ada logika baru di sini — cuma memetakan action → fungsi lokal,
    supaya perilaku lewat MQTT identik dengan lewat endpoint HTTP.
    (`status` & `camera_worker` didefinisikan di bawah; nama global baru
    di-resolve saat fungsi ini dipanggil, yaitu di startup.)
    """

    @remote.action("status")
    def _act_status(args):
        return status()

    @remote.action("camera_start")
    def _act_camera_start(args):
        ok = camera_worker.start()
        return {"started": ok, "running": camera_worker.running, "error": camera_worker.error}

    @remote.action("camera_stop")
    def _act_camera_stop(args):
        camera_worker.stop()
        return {"running": camera_worker.running}

    @remote.action("actuator")
    def _act_actuator(args):
        cmd = str(args.get("cmd", "")).strip()
        if cmd not in {"organik", "anorganik", "B3", "reset"}:
            raise ValueError(f"Perintah tidak valid: {cmd!r}")
        # "reset" dulu dilempar ke publish_cmd (MQTT topik smartbin/*/cmd) yang
        # TIDAK ADA subscriber-nya di sisi STM32 — jadi selalu lapor ok:true tapi
        # mekaniknya diam. Sekarang semua perintah lewat serial; kirim_ke_stm32
        # sendiri yang jatuh ke MQTT hanya kalau serial benar-benar mati.
        return kirim_ke_stm32(cmd)

    def _snapshot():
        with sensor_lock:
            sensor_ok = bool(latest_sensor)
        return {
            "camera":       "running" if camera_worker.running else "stopped",
            "camera_error": camera_worker.error,
            "last_detection": camera_worker.last,
            "serial_stm32": "connected" if (arduino and arduino.is_open) else "disconnected",
            "sensor_data":  "ada" if sensor_ok else "belum ada",
            "last_seq":     _seq_counter,
        }

    remote.set_state_fn(_snapshot)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _is_running
    _is_running = True

    _register_remote_actions()
    remote.start()      # tee stdout + thread state/log — sebelum init_mqtt biar log startup ketangkep

    init_all_serial()   # <-- SATU-SATUNYA tempat buka serial STM32
    init_mqtt()
    init_http_compare() # <-- jalur HTTP perbandingan (kalau COMPARE_HTTP=1)
    start_dispatcher()  # <-- SATU-SATUNYA thread pembaca serial
    if SERIAL_WATCHDOG:
        threading.Thread(target=_watchdog_loop, daemon=True, name="serial-watchdog").start()

    if AUTO_START_CAM:
        if camera_worker.start():
            print("[CAM] Auto-start kamera Raspi aktif (AUTO_START_CAM=1) — milah otomatis tanpa web.")
        else:
            print(f"[CAM] Auto-start kamera gagal: {camera_worker.error}")

    if CAMERA_PUSH:
        threading.Thread(target=_camera_push_loop, daemon=True).start()
        print(f"[CAM] Push frame monitor aktif → {BACKEND_HTTP_URL}/camera/frame tiap {CAMERA_PUSH_SEC}s")

    print("[+] Startup selesai: STM32, MQTT, Dispatcher semua aktif.")
    # Dicetak eksplisit supaya ketahuan ke mana data pergi tanpa perlu buka /status.
    print(f"[+] Telemetri sensor → MQTT {MQTT_HOST}:{MQTT_PORT} topik {TOPIC_SENSOR}")
    print(f"[+] Hasil klasifikasi → topik {TOPIC_CLASSIFICATION}")
    if COMPARE_HTTP:
        print(f"[+] Jalur HTTP tambahan aktif → {BACKEND_HTTP_URL}")
    yield

    _is_running = False
    remote.stop()       # tandai offline dgn sopan + kembalikan sys.stdout
    camera_worker.stop()
    if _dispatcher_thread is not None:
        _dispatcher_thread.join(timeout=2)
    close_all_serial()
    if _mqtt_client is not None:
        _mqtt_client.loop_stop()
        _mqtt_client.disconnect()
        print("[+] MQTT ditutup.")

app = FastAPI(title="EcoSort AI Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========================================================
# PYDANTIC MODELS
# ========================================================
class TipsReq(BaseModel):
    kategori: str

class AskReq(BaseModel):
    pertanyaan: str
    kategori: str = ""

class ArduinoReq(BaseModel):
    perintah: str

class MqttCmdReq(BaseModel):
    cmd: str

# ========================================================
# ROUTES
# ========================================================
@app.get("/status")
def status():
    with sensor_lock:
        sensor_ok = bool(latest_sensor)
    return {
        "status": "online", "model": os.path.basename(MODEL_PATH),
        "input_size": f"{IN_W}x{IN_H}", "classes": CLASS_NAMES,
        "gemini": "ready" if gemini_client else "unavailable",
        "serial_stm32": "connected" if (arduino and arduino.is_open) else "disconnected",
        "mqtt": "connected" if mqtt_connected else "disconnected",
        "sensor_data": "ada" if sensor_ok else "belum ada",
        # Umur data = satu-satunya bukti board BENERAN hidup. serial_stm32
        # "connected" cuma berarti port kebuka di OS; board hang tetap "connected".
        "stm32": {
            "umur_data_detik": (None if umur_data_stm32() == float("inf")
                                else round(umur_data_stm32(), 1)),
            "belum_pernah_ada_data": umur_data_stm32() == float("inf"),
            "sepi_detik": round(sepi_stm32(), 1),
            "hidup": umur_data_stm32() < SERIAL_QUIET_SEC,
            "watchdog": SERIAL_WATCHDOG,
            "ambang_sepi_detik": SERIAL_QUIET_SEC,
            "jumlah_reset": _wd_resets,
        },
        "camera": "running" if camera_worker.running else "stopped",
        "forward": {
            "mqtt": True,   # telemetri sensor selalu dikirim ke MQTT
            "http_compare": COMPARE_HTTP and (_http_q is not None),
        },
        "last_seq": _seq_counter,
    }

# ================= KONTROL KAMERA REALTIME =================
@app.post("/camera/start")
def camera_start():
    ok = camera_worker.start()
    return {"status": "success" if ok else "error", "running": camera_worker.running, "error": camera_worker.error}

@app.post("/camera/stop")
def camera_stop():
    camera_worker.stop()
    return {"status": "success", "running": camera_worker.running}

@app.get("/camera/status")
def camera_status():
    return {
        "running": camera_worker.running,
        "has_opencv": _HAS_CV2,
        "camera_index": CAMERA_INDEX,
        "roi": {"frac": ROI_FRAC, "dx": ROI_DX, "dy": ROI_DY, "classify_roi": CLASSIFY_ROI},
        "trigger": {"diff_color": DIFF_COLOR,
                    "obj_diff": OBJECT_DIFF, "pixel_delta": OBJECT_PIXEL_DELTA,
                    "area_min": OBJECT_AREA_MIN, "baseline_alpha": BASELINE_ALPHA,
                    "clear_diff": CLEAR_DIFF, "clear_area": CLEAR_AREA,
                    "still_move": STILL_MOVE, "rearm_still_need": REARM_STILL_NEED,
                    "rearm_max_wait": REARM_MAX_WAIT, "rearm_grace_sec": REARM_GRACE_SEC},
        "detektor": camera_worker.debug,   # kondisi live: kenapa trigger / tidak
        "model_error": MODEL_LOAD_ERROR,
        "ensemble": {"aktif": ENSEMBLE_ACTIVE, "mode": ENSEMBLE_MODE,
                     "utama": _primary_path, "penjaga": MODEL_PATH,
                     "guard_weight": GUARD_WEIGHT},
        "conf_threshold": CONF_THRESHOLD,
        "motion_threshold": MOTION_THRESHOLD,
        "cooldown_sec": COOLDOWN_SEC,           # jeda saat tak ada aktuasi
        "actuator_lock_sec": ACTUATOR_TIMES,    # lock kamera per-kategori saat aktuasi
        "last_detection": camera_worker.last,
        "error": camera_worker.error,
    }

@app.get("/camera/snapshot")
def camera_snapshot(mask: int = 1):
    """Foto ROI terakhir + tandai piksel yang dianggap BERUBAH (merah).

    Ada buat menjawab satu pertanyaan yang selama ini cuma bisa ditebak dari
    angka: kalau status bilang "platform belum kosong" padahal kelihatan kosong,
    yang dianggap objek itu SEBENARNYA apa? Kalau merahnya nempel di satu benda
    → memang ada yang nyangkut. Kalau merahnya nyebar rata seluruh ROI → itu
    pergeseran cahaya/baseline, bukan benda.

    ?mask=0 buat foto polos tanpa tanda merah.
    """
    roi, dm = camera_worker.last_roi, camera_worker.last_diff
    if roi is None or dm is None:
        return {"status": "kosong", "message": "Kamera belum jalan / belum ada frame"}

    img = roi.copy()
    if mask:
        # Merah = piksel yang lewat ambang OBJECT_PIXEL_DELTA, yaitu persis
        # piksel yang dihitung jadi obj_area.
        kena = (dm > OBJECT_PIXEL_DELTA)
        img[kena] = (0.4 * img[kena] + 0.6 * np.array([0, 0, 255])).astype(img.dtype)

    d = camera_worker.debug or {}
    baris = [
        f"diff {d.get('obj_diff')} (ambang {OBJECT_DIFF:.0f})",
        f"area {d.get('obj_area_pct')}% (ambang {OBJECT_AREA_MIN*100:.0f}%)",
        f"armed {d.get('armed')}  stm32 {d.get('stm32_siap')}",
    ]
    for i, t in enumerate(baris):
        y = 18 + i * 20
        cv2.putText(img, t, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
        cv2.putText(img, t, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    ok, buf = cv2.imencode(".jpg", img)
    if not ok:
        return {"status": "error", "message": "Gagal encode JPEG"}
    return Response(content=buf.tobytes(), media_type="image/jpeg")

@app.get("/")
def home():
    if os.path.exists(os.path.join(FRONTEND_DIR, "index.html")):
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
    return {"message": "Frontend index.html tidak ditemukan!"}

@app.post("/predict/")
async def predict(file: UploadFile = File(...)):
    if interpreter is None:
        return {"error": "Model tidak ter-load"}
    try:
        image_bytes = await file.read()
        image       = Image.open(io.BytesIO(image_bytes))
        kategori, confidence = await asyncio.to_thread(_predict, image)
        print(f"[+] Deteksi: {kategori} ({confidence:.1%})")
        aktuator = kirim_ke_stm32(kategori)
        report_classification(kategori, confidence)   # <- lapor ke backend SmartBIN (analitik/deposit)
        return {"status": "success", "hasil": [{"kategori": kategori, "confidence": confidence}], "aktuator": aktuator}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.post("/arduino/")
async def arduino_manual(req: ArduinoReq):
    cmd = req.perintah.strip()
    if cmd not in STM32_CMD_VALID:
        return {"status": "error", "message": f"Perintah tidak valid. Pilih: {sorted(STM32_CMD_VALID)}"}
    hasil = kirim_ke_stm32(cmd)
    return {"status": "success" if hasil["ok"] else "error", **hasil}

@app.get("/sensor/latest")
def sensor_latest():
    with sensor_lock:
        if not latest_sensor:
            return {"status": "kosong", "message": "Belum ada data dari STM32"}
        return {"status": "ok", "data": latest_sensor}

@app.get("/sensor/battery")
def sensor_battery():
    with sensor_lock:
        batt = latest_sensor.get("battery")
    if not batt:
        return {"status": "kosong", "message": "Belum ada data baterai"}
    return {"status": "ok", "battery": batt}

@app.post("/mqtt/cmd/")
async def mqtt_cmd(req: MqttCmdReq):
    cmd = req.cmd.strip()
    if cmd not in {"organik", "anorganik", "B3", "reset"}:
        return {"status": "error", "message": f"Cmd tidak valid: {cmd}"}
    ok = publish_cmd(cmd)
    return {"status": "success" if ok else "error", "cmd": cmd, "mqtt_connected": mqtt_connected}

@app.get("/mqtt/status")
def mqtt_status():
    return {"connected": mqtt_connected, "broker": MQTT_HOST, "topic_sub": TOPIC_SENSOR, "topic_cmd": TOPIC_CMD}

@app.post("/genai/tips/")
async def genai_tips(req: TipsReq):
    if gemini_client is None:
        return {"status": "success", "tips": STATIC_TIPS.get(req.kategori, ""), "source": "static"}
    if req.kategori in tips_cache:
        return {"status": "success", "tips": tips_cache[req.kategori], "cached": True}
    try:
        resp = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-2.5-flash",
            contents=f"Tips singkat sampah {req.kategori} dalam 3 bullet poin.",
        )
        tips_cache[req.kategori] = resp.text
        return {"status": "success", "tips": resp.text, "cached": False, "source": "gemini"}
    except Exception:
        return {"status": "success", "tips": STATIC_TIPS.get(req.kategori, ""), "source": "static"}

@app.post("/genai/ask/")
async def genai_ask(req: AskReq):
    if gemini_client is None:
        return {"status": "success", "jawaban": "Maaf, AI sedang tidak tersedia."}
    try:
        resp = await asyncio.to_thread(
            gemini_client.models.generate_content,
            model="gemini-2.5-flash",
            contents=f"Jawab singkat maksimal 3 kalimat: {req.pertanyaan}",
        )
        return {"status": "success", "jawaban": resp.text}
    except Exception:
        return {"status": "success", "jawaban": "Gagal menghubungi AI."}

if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)