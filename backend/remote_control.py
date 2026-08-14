"""
====================================================
Remote Control Bridge (MQTT)
====================================================
Bikin main.py bisa dikendalikan dari LUAR NAT lewat broker HiveMQ
yang sama — tanpa VPN/Tailscale/port-forward. Pi cuma butuh koneksi
keluar (outbound) ke broker, jadi WiFi kost/hotspot pun jalan.

Topik dinamespace di bawah `device/` supaya TIDAK tabrakan dengan
TOPIC_CMD (`smartbin/{id}/cmd`) yang sudah dipakai kirim perintah
aktuator ke STM32:

  smartbin/{NODE_ID}/device/cmd    <- MASUK  : perintah dari backend
  smartbin/{NODE_ID}/device/ack    -> KELUAR : hasil tiap perintah
  smartbin/{NODE_ID}/device/state  -> KELUAR : snapshot status (retained)
  smartbin/{NODE_ID}/device/log    -> KELUAR : streaming log (opt-in, auto-mati)

Perilaku penting:
  * LWT  — broker otomatis publish {"online": false} kalau Pi mati mendadak,
           jadi status retained tidak pernah nyangkut "online" selamanya.
  * Dedup — QoS 1 itu at-least-once; perintah bisa nyampe dobel. Tiap cmd
           wajib punya "id", ack lama dikirim ulang untuk id yang sama
           (idempoten — kamera tidak kestart 2x).
  * Log   — default MATI. Dinyalakan per-sesi dengan TTL supaya tidak
           membakar kuota HiveMQ free tier saat tidak ada yang nonton.
"""

import json
import os
import subprocess
import sys
import threading
import time
from collections import OrderedDict, deque

# ========================================================
# CONFIG
# ========================================================
REMOTE_ENABLED   = os.environ.get("REMOTE_CONTROL", "1") == "1"
STATE_EVERY_SEC  = float(os.environ.get("REMOTE_STATE_SEC", "15"))    # heartbeat state
LOG_FLUSH_SEC    = float(os.environ.get("REMOTE_LOG_FLUSH_SEC", "0.7"))
LOG_MAX_BATCH    = int(os.environ.get("REMOTE_LOG_MAX_LINES", "40"))  # max baris per flush
LOG_QUEUE_MAX    = int(os.environ.get("REMOTE_LOG_QUEUE", "500"))     # buffer, drop yang tertua
LOG_DEFAULT_TTL  = float(os.environ.get("REMOTE_LOG_TTL", "300"))     # detik, auto-mati
DEDUP_MAX        = 64

# Perintah yang boleh diteruskan ke aktuator (whitelist, samain dgn main.py)
ACTUATOR_CMDS = {"organik", "anorganik", "B3", "reset"}


class _StdoutTee:
    """Bayangi sys.stdout: tetap cetak ke terminal, sekaligus antre ke MQTT.

    main.py pakai print() di mana-mana (bukan modul logging), jadi menyadap
    di level stdout adalah cara paling tidak invasif — nol perubahan di
    call-site. Thread yang lagi nge-publish ditandai lewat threading.local
    supaya print() dari publisher sendiri tidak ikut tertangkap (rekursi).
    """

    def __init__(self, original, sink):
        self._original = original
        self._sink     = sink
        self._buf      = ""
        self._lock     = threading.Lock()
        self._local    = threading.local()

    def write(self, s):
        self._original.write(s)
        # Jangan tangkap output dari thread publisher sendiri → infinite loop.
        if getattr(self._local, "muted", False):
            return
        try:
            with self._lock:
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line.strip():
                        self._sink(line)
        except Exception:
            pass  # logging tidak boleh menjatuhkan aplikasi

    def flush(self):
        self._original.flush()

    def mute(self):
        self._local.muted = True

    def isatty(self):
        return getattr(self._original, "isatty", lambda: False)()


class RemoteControl:
    """Jembatan MQTT ⇄ fungsi-fungsi lokal main.py."""

    def __init__(self, node_id):
        self.node_id     = node_id
        self.base        = f"smartbin/{node_id}/device"
        self.T_CMD       = f"{self.base}/cmd"
        self.T_ACK       = f"{self.base}/ack"
        self.T_STATE     = f"{self.base}/state"
        self.T_LOG       = f"{self.base}/log"

        self._client     = None
        self._actions    = {}
        self._state_fn   = lambda: {}
        self._started_at = time.time()

        # dedup: id perintah -> payload ack terakhir
        self._seen       = OrderedDict()
        self._seen_lock  = threading.Lock()

        # log streaming
        self._log_q      = deque(maxlen=LOG_QUEUE_MAX)
        self._log_lock   = threading.Lock()
        self._log_until  = 0.0          # epoch; 0 = mati
        self._log_dropped = 0
        self._tee        = None
        self._stop       = threading.Event()

    # ====================================================
    # REGISTRASI
    # ====================================================
    def action(self, name):
        """Decorator: daftarkan handler perintah. Handler(args: dict) -> dict."""
        def deco(fn):
            self._actions[name] = fn
            return fn
        return deco

    def set_state_fn(self, fn):
        """fn() -> dict, dipakai sebagai isi snapshot state."""
        self._state_fn = fn

    # ====================================================
    # WIRING KE PAHO
    # ====================================================
    def attach(self, client):
        """Panggil SETELAH client dibuat, SEBELUM client.connect().

        Wajib sebelum connect karena will_set() hanya berlaku kalau
        didaftarkan sebelum handshake CONNECT.
        """
        if not REMOTE_ENABLED:
            return
        self._client = client
        offline = json.dumps({
            "online": False,
            "node_id": self.node_id,
            "reason": "lwt",
        })
        client.will_set(self.T_STATE, offline, qos=1, retain=True)

    def on_connect(self, client):
        """Panggil dari _on_connect main.py saat rc == 0."""
        if not REMOTE_ENABLED:
            return
        self._client = client
        client.subscribe(self.T_CMD, qos=1)
        print(f"[Remote] Siap — dengar perintah di {self.T_CMD}")
        self.publish_state()

    def on_message(self, client, userdata, msg):
        """Pasang sebagai client.on_message (main.py belum memakainya)."""
        if msg.topic != self.T_CMD:
            return  # abaikan topik lain (mis. status yang di-subscribe main.py)
        try:
            req = json.loads(msg.payload.decode())
        except Exception as e:
            self._publish(self.T_ACK, {"ok": False, "error": f"payload bukan JSON: {e}"})
            return
        threading.Thread(target=self._dispatch, args=(req,), daemon=True).start()

    # ====================================================
    # DISPATCH
    # ====================================================
    def _dispatch(self, req):
        cmd_id = str(req.get("id") or "")
        action = str(req.get("action") or "")
        args   = req.get("args") or {}

        if not cmd_id:
            self._publish(self.T_ACK, {"ok": False, "action": action,
                                       "error": "field 'id' wajib (untuk dedup)"})
            return

        # QoS 1 = at-least-once → id yang sama bisa datang 2x. Kirim ulang ack
        # lama alih-alih mengeksekusi ulang.
        with self._seen_lock:
            if cmd_id in self._seen:
                cached = self._seen[cmd_id]
                print(f"[Remote] Duplikat id={cmd_id} — kirim ulang ack lama.")
                self._publish(self.T_ACK, {**cached, "duplicate": True})
                return

        fn = self._actions.get(action)
        if fn is None:
            ack = {"ok": False, "id": cmd_id, "action": action,
                   "error": f"action tidak dikenal: {action}",
                   "available": sorted(self._actions)}
        else:
            try:
                result = fn(args) or {}
                ack = {"ok": True, "id": cmd_id, "action": action, "result": result}
            except Exception as e:
                ack = {"ok": False, "id": cmd_id, "action": action, "error": str(e)}

        ack["ts"] = time.time()

        with self._seen_lock:
            self._seen[cmd_id] = ack
            while len(self._seen) > DEDUP_MAX:
                self._seen.popitem(last=False)

        self._publish(self.T_ACK, ack, qos=1)
        self.publish_state()   # state ikut kekinian setelah tiap perintah

    # ====================================================
    # STATE
    # ====================================================
    def publish_state(self):
        if not REMOTE_ENABLED or self._client is None:
            return
        try:
            snap = dict(self._state_fn() or {})
        except Exception as e:
            snap = {"state_error": str(e)}
        snap.update({
            "online":     True,
            "node_id":    self.node_id,
            "uptime_sec": round(time.time() - self._started_at, 1),
            "log_stream": self._log_active(),
            "ts":         time.time(),
        })
        self._publish(self.T_STATE, snap, qos=1, retain=True)

    def _state_loop(self):
        while not self._stop.wait(STATE_EVERY_SEC):
            self.publish_state()

    # ====================================================
    # LOG STREAMING
    # ====================================================
    def _log_active(self):
        return time.time() < self._log_until

    def _log_sink(self, line):
        if not self._log_active():
            return
        with self._log_lock:
            if len(self._log_q) == self._log_q.maxlen:
                self._log_dropped += 1   # deque penuh → yang tertua terbuang
            self._log_q.append(line)

    def enable_log(self, on=True, ttl=None):
        if on:
            self._log_until = time.time() + float(ttl or LOG_DEFAULT_TTL)
        else:
            self._log_until = 0.0
            with self._log_lock:
                self._log_q.clear()
                self._log_dropped = 0
        return {"log_stream": self._log_active(),
                "until": self._log_until,
                "ttl_sec": round(max(0.0, self._log_until - time.time()), 1)}

    def _log_loop(self):
        was_active = False
        while not self._stop.wait(LOG_FLUSH_SEC):
            active = self._log_active()

            # TTL habis → beri tahu sekali supaya klien tahu kenapa log berhenti.
            if was_active and not active:
                self._publish(self.T_LOG, {"lines": [], "stream_ended": "ttl_expired",
                                           "ts": time.time()})
                self.publish_state()
            was_active = active
            if not active:
                continue

            with self._log_lock:
                if not self._log_q:
                    continue
                batch = [self._log_q.popleft()
                         for _ in range(min(LOG_MAX_BATCH, len(self._log_q)))]
                dropped, self._log_dropped = self._log_dropped, 0

            payload = {"lines": batch, "ts": time.time()}
            if dropped:
                payload["dropped"] = dropped   # jujur soal baris yang hilang
            # QoS 0: log itu best-effort. QoS 1 bikin antrean menumpuk saat
            # koneksi jelek, dan log basi lebih buruk daripada log hilang.
            self._publish(self.T_LOG, payload, qos=0)

    # ====================================================
    # START
    # ====================================================
    def start(self):
        """Nyalakan tee stdout + thread state/log. Panggil sekali saat startup."""
        if not REMOTE_ENABLED:
            print("[Remote] REMOTE_CONTROL=0 — bridge tidak aktif.")
            return

        # Action bawaan (yang butuh objek main.py didaftarkan dari sana).
        self._actions.setdefault("ping", lambda a: {"pong": True})
        self._actions.setdefault(
            "log_stream",
            lambda a: self.enable_log(bool(a.get("on", True)), a.get("ttl")),
        )
        # Matiin/restart raspi dari jarak jauh. sudo karena shutdown butuh root;
        # main.py harus jalan sebagai user yang punya NOPASSWD sudo shutdown,
        # atau seluruh proses dijalankan sebagai root.
        self._actions.setdefault(
            "shutdown",
            lambda a: self._power("shutdown", "poweroff"),
        )
        self._actions.setdefault(
            "reboot",
            lambda a: self._power("reboot", "reboot"),
        )
        # Restart main.py tanpa restart OS. Socket MQTT ikut ditutup — kalau
        # belum ada watchdog systemd yang restart otomatis, Pi jadi offline.
        # Didesain buat dipakai bersama unit systemd (Restart=always) di Pi.
        self._actions.setdefault(
            "run",
            lambda a: self._restart_main("run"),
        )

        self._tee = _StdoutTee(sys.stdout, self._log_sink)
        sys.stdout = self._tee

        threading.Thread(target=self._state_loop, daemon=True, name="remote-state").start()
        threading.Thread(target=self._log_pump,  daemon=True, name="remote-log").start()

    def _power(self, name, verb):
        """Jalankan shutdown/reboot. Dijalankan di thread terpisah supaya ack
        masih sempat ke-publish SEBELUM sistem mati (kalau jalan di thread
        dispatcher, proses keburu shutdown dan ack gak keluar).

        Kegagalan TIDAK dibungkam: kalau `sudo` minta password (belum ada
        NOPASSWD) atau rc != 0, errornya di-print — kelihatan di log remote
        (kalau streaming nyala) maupun di stderr journal systemd. Sebelumnya
        return code dibuang, jadi shutdown yang gagal terlihat sukses."""
        import subprocess as _sp
        import threading as _t

        def _do():
            try:
                r = _sp.run(["sudo", verb], capture_output=True, text=True, timeout=30)
                if r.returncode != 0:
                    msg = (r.stderr or r.stdout).strip()
                    print(f"[Remote] {name} GAGAL rc={r.returncode}: {msg or 'tanpa pesan error'}")
                else:
                    print(f"[Remote] {name} dipicu (rc=0).")
            except _sp.TimeoutExpired:
                print(f"[Remote] {name} timeout 30s — sudo menggantung? cek /etc/sudoers.d")
            except Exception as _e:
                print(f"[Remote] {name} gagal: {_e}")

        _t.Thread(target=_do, daemon=True).start()
        # ack tetap "scheduled" — hasil nyata tampil lewat log di atas, karena
        # kalau sukses sistemnya langsung mati sebelum sempat balas ack lain.
        return {"action": name, "scheduled": True}

    def _restart_main(self, name):
        """Jalankan ulang proses main.py.

        Proses keluar keras (os._exit) ~1 detik setelah ack sempat ter-publish —
        broker lalu mengirim LWT offline, dan unit systemd (Restart=always)
        menyalakan ulang main.py. Tanpa systemd, Pi jadi offline sampai ada
        yang SSH masuk.
        """
        import threading as _t

        def _die():
            time.sleep(1.0)          # sisakan waktu buat ack ke-publish dulu
            print("[Remote] Restart main.py ...")
            os._exit(0)              # keluar keras → socket tutup → LWT offline

        _t.Thread(target=_die, daemon=True).start()
        return {"action": name, "scheduled": True, "restart_via": "systemd"}

    def _log_pump(self):
        # Thread ini ikut nge-print saat error → bisukan supaya tidak rekursi.
        if self._tee is not None:
            self._tee.mute()
        self._log_loop()

    def stop(self):
        self._stop.set()
        if self._tee is not None:
            sys.stdout = self._tee._original
            self._tee = None
        # Tandai offline dengan sopan (bukan lewat LWT) saat shutdown normal.
        if self._client is not None and REMOTE_ENABLED:
            self._publish(self.T_STATE, {
                "online": False, "node_id": self.node_id, "reason": "shutdown",
                "ts": time.time(),
            }, qos=1, retain=True)

    # ====================================================
    # UTIL
    # ====================================================
    def _publish(self, topic, obj, qos=0, retain=False):
        if self._client is None:
            return
        try:
            self._client.publish(topic, json.dumps(obj), qos=qos, retain=retain)
        except Exception as e:
            # Sengaja pakai stderr: stdout lagi di-tee, dan kegagalan publish
            # tidak boleh memicu publish berikutnya.
            print(f"[Remote] Gagal publish {topic}: {e}", file=sys.stderr)
