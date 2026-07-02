"""
====================================================
Backend API Client
====================================================
Ambil metrik byterate/throughput transmisi dari core server (backend Node.js)
lewat HTTP GET, lalu isi ke dashboard state.

Backend adalah titik terima MQTT sebenarnya, jadi byterate diukur di sana dan
CLI ini cukup menampilkannya. Aman kalau backend mati — nilai terakhir bertahan
dan status di-set offline, loop tidak crash.
====================================================
"""

import requests

from config import BACKEND_API_URL, API_TIMEOUT
from dashboard.state import state
from logger.log_manager import logger


class APIClient:

    ENDPOINT = "/monitoring/transmission"

    # supaya warning tidak spam tiap detik saat backend mati
    def __init__(self):
        self._error_logged = False

    # ==================================================

    def poll(self):
        url = f"{BACKEND_API_URL}{self.ENDPOINT}"

        try:
            resp = requests.get(url, timeout=API_TIMEOUT)
            resp.raise_for_status()
            body = resp.json()
        except Exception as e:
            # Backend tidak reachable — pertahankan nilai terakhir, tandai offline.
            state.wifi.connected = False
            if not self._error_logged:
                logger.warning(f"Backend API unreachable: {e}")
                self._error_logged = True
            return

        if self._error_logged:
            logger.success("Backend API reachable again")
            self._error_logged = False

        data = body.get("data", {})
        self._apply(data)

    # ==================================================

    def _apply(self, data):
        aggregate = data.get("aggregate", {})
        nodes     = data.get("nodes", [])

        # Metrik kumulatif & laju → pakai agregat semua node
        state.wifi.packets      = aggregate.get("packets",  0)
        state.wifi.received     = aggregate.get("received", 0)
        state.wifi.loss         = aggregate.get("loss",     0)
        state.wifi.loss_percent = aggregate.get("lossPercent",   0.0)
        state.wifi.packet_rate  = aggregate.get("packetRate",    0.0)
        state.wifi.throughput   = aggregate.get("throughputKBps", 0.0)

        # Latency / seq / umur paket → ambil dari node paling baru aktif
        freshest = None
        for n in nodes:
            age = n.get("lastPacketAgeSec")
            if age is None:
                continue
            if freshest is None or age < freshest.get("lastPacketAgeSec", 1e9):
                freshest = n

        if freshest is not None:
            state.wifi.latency         = freshest.get("latencyMs", 0.0)
            state.wifi.last_sequence   = freshest.get("lastSeq",   0)
            state.wifi.last_packet_age = freshest.get("lastPacketAgeSec", 0.0)
            # dianggap online kalau paket terakhir masih dalam window 10 detik
            state.wifi.connected = freshest.get("lastPacketAgeSec", 999) <= 10.0
        else:
            state.wifi.connected = False


api_client = APIClient()
