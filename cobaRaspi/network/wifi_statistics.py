"""
====================================================
WiFi Statistics
====================================================
Hitung packet rate, latency, throughput, packet loss
untuk koneksi Raspberry Pi A (WiFi) / SmartBIN.

Catatan latency:
  Latency = receive_time - send_timestamp
  Karena clock Windows dan pengirim mungkin tidak sinkron,
  dipakai abs() dan nilai negatif di-skip.
  Untuk akurasi tinggi, sinkronkan NTP di kedua sisi.
====================================================
"""

import time
from collections import deque

from dashboard.state import state
from logger.log_manager import logger


class WiFiStatistics:

    WINDOW_SIZE = 10.0              # detik sliding window

    HIGH_LATENCY_THRESHOLD = 500.0  # ms
    LOSS_WARNING_THRESHOLD = 1      # packet

    def __init__(self):

        self.packet_window  = deque()
        self.latency_window = deque()

        self.previous_sequence = None

        # simpan nilai terakhir — tidak reset ke 0 saat window kosong
        self._last_packet_rate = 0.0
        self._last_throughput  = 0.0
        self._last_latency     = 0.0

        # flag warning agar tidak spam
        self.timeout_warning_sent  = False
        self.loss_warning_sent     = False
        self.latency_warning_sent  = False

    # ==================================================

    def process_packet(
        self,
        payload_size,
        sequence=None,
        timestamp=None,
        receive_time=None,
    ):

        now = receive_time if receive_time is not None else time.time()

        state.wifi.connected       = True
        state.wifi.last_packet_time = now
        self.timeout_warning_sent  = False

        # ------------------------------------------
        # Sequence
        # ------------------------------------------

        if sequence is not None:
            state.wifi.last_sequence = sequence

        state.wifi.packets  += 1
        state.wifi.received += 1

        self.packet_window.append((now, payload_size))

        # ------------------------------------------
        # Packet Loss
        # ------------------------------------------

        if sequence is not None:

            if self.previous_sequence is not None:
                expected = self.previous_sequence + 1
                if sequence > expected:
                    lost = sequence - expected
                    state.wifi.loss += lost

            self.previous_sequence = sequence

        if state.wifi.packets > 0:
            state.wifi.loss_percent = (
                state.wifi.loss / state.wifi.packets
            ) * 100

        # ------------------------------------------
        # Latency
        # Hitung dari selisih receive_time vs send timestamp.
        # Kalau negatif (clock skew), pakai abs().
        # Kalau timestamp tidak ada, skip.
        # ------------------------------------------

        if timestamp is not None and receive_time is not None:
            raw_latency = (receive_time - timestamp) * 1000
            latency = abs(raw_latency)
            # Abaikan kalau terlalu besar (>10 detik) — kemungkinan clock skew ekstrem
            if latency < 10_000:
                self.latency_window.append((now, latency))

        self._update(now)

    # ==================================================

    def update(self):
        """Dipanggil dari main loop untuk refresh last_packet_age."""
        self._update(time.time())

    # ==================================================

    def _update(self, now):

        # ------------------------------------------
        # Buang data di luar window
        # ------------------------------------------

        while (
            self.packet_window
            and now - self.packet_window[0][0] > self.WINDOW_SIZE
        ):
            self.packet_window.popleft()

        while (
            self.latency_window
            and now - self.latency_window[0][0] > self.WINDOW_SIZE
        ):
            self.latency_window.popleft()

        # ------------------------------------------
        # Packet Rate & Throughput
        # Pertahankan nilai terakhir kalau window kosong
        # ------------------------------------------

        if self.packet_window:

            duration = now - self.packet_window[0][0]

            # Minimal 1 detik biar rate tidak meledak di paket pertama
            duration = max(duration, 1.0)

            total_bytes = sum(p[1] for p in self.packet_window)

            self._last_packet_rate = len(self.packet_window) / duration
            self._last_throughput  = total_bytes / duration / 1024

        # selalu assign dari _last — tidak pernah reset ke 0
        state.wifi.packet_rate = self._last_packet_rate
        state.wifi.throughput  = self._last_throughput

        # ------------------------------------------
        # Latency — pertahankan nilai terakhir
        # ------------------------------------------

        if self.latency_window:
            self._last_latency = (
                sum(l[1] for l in self.latency_window)
                / len(self.latency_window)
            )

        state.wifi.latency = self._last_latency

        # ------------------------------------------
        # Last Packet Age
        # ------------------------------------------

        if state.wifi.last_packet_time > 0:
            state.wifi.last_packet_age = now - state.wifi.last_packet_time

        # ------------------------------------------
        # Health Monitoring
        # ------------------------------------------

        # Packet Loss
        if (
            state.wifi.loss >= self.LOSS_WARNING_THRESHOLD
            and not self.loss_warning_sent
        ):
            logger.warning(
                f"Raspberry Pi A (WiFi) Packet Loss Detected "
                f"(Loss={state.wifi.loss})"
            )
            self.loss_warning_sent = True

        # High Latency
        if (
            state.wifi.latency >= self.HIGH_LATENCY_THRESHOLD
            and not self.latency_warning_sent
        ):
            logger.warning(
                f"Raspberry Pi A (WiFi) High Latency "
                f"({state.wifi.latency:.2f} ms)"
            )
            self.latency_warning_sent = True

        # Connection Timeout
        if state.wifi.last_packet_age > self.WINDOW_SIZE:
            state.wifi.connected = False
            if not self.timeout_warning_sent:
                logger.warning(
                    "Raspberry Pi A (WiFi) Connection Timeout"
                )
                self.timeout_warning_sent = True


wifi_statistics = WiFiStatistics()
