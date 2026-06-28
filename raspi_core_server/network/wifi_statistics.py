import time
from collections import deque

from dashboard.state import state
from logger.log_manager import logger


class WiFiStatistics:

    WINDOW_SIZE = 5.0

    HIGH_LATENCY_THRESHOLD = 100.0      # ms
    LOSS_WARNING_THRESHOLD = 1          # packet

    def __init__(self):

        self.packet_window = deque()
        self.latency_window = deque()

        self.previous_sequence = None

        # Prevent repeated warning
        self.timeout_warning_sent = False
        self.loss_warning_sent = False
        self.latency_warning_sent = False

    # ==================================================

    def process_packet(
        self,
        payload_size,
        sequence=None,
        timestamp=None
    ):

        now = time.time()

        state.wifi.connected = True
        state.wifi.last_packet_time = now

        # reset timeout flag
        self.timeout_warning_sent = False

        if sequence is not None:
            state.wifi.last_sequence = sequence

        state.wifi.packets += 1
        state.wifi.received += 1

        self.packet_window.append(
            (now, payload_size)
        )

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
                state.wifi.loss /
                state.wifi.packets
            ) * 100

        # ------------------------------------------
        # Latency
        # ------------------------------------------

        if timestamp is not None:

            latency = (
                now - timestamp
            ) * 1000

            self.latency_window.append(
                (now, latency)
            )

        self.update()

    # ==================================================

    def update(self):

        now = time.time()

        while (
            self.packet_window
            and
            now - self.packet_window[0][0]
            > self.WINDOW_SIZE
        ):
            self.packet_window.popleft()

        while (
            self.latency_window
            and
            now - self.latency_window[0][0]
            > self.WINDOW_SIZE
        ):
            self.latency_window.popleft()

        # ------------------------------------------
        # Packet Rate
        # ------------------------------------------

        if self.packet_window:

            duration = max(
                now - self.packet_window[0][0],
                1e-6
            )

            total_bytes = sum(
                p[1]
                for p in self.packet_window
            )

            state.wifi.packet_rate = (
                len(self.packet_window)
                / duration
            )

            state.wifi.throughput = (
                total_bytes
                / duration
                / 1024
            )

        else:

            state.wifi.packet_rate = 0.0
            state.wifi.throughput = 0.0

        # ------------------------------------------
        # Average Latency
        # ------------------------------------------

        if self.latency_window:

            state.wifi.latency = (
                sum(
                    latency[1]
                    for latency
                    in self.latency_window
                )
                /
                len(self.latency_window)
            )

        # sengaja mempertahankan nilai latency terakhir

        # ------------------------------------------
        # Last Packet Age
        # ------------------------------------------

        if state.wifi.last_packet_time > 0:

            state.wifi.last_packet_age = (
                now -
                state.wifi.last_packet_time
            )

        # ------------------------------------------
        # Health Monitoring
        # ------------------------------------------

        # Packet Loss

        if (
            state.wifi.loss >= self.LOSS_WARNING_THRESHOLD
            and
            not self.loss_warning_sent
        ):

            logger.warning(
                f"Raspberry Pi A (WiFi) Packet Loss Detected "
                f"(Loss={state.wifi.loss})"
            )

            self.loss_warning_sent = True

        # High Latency

        if (
            state.wifi.latency >= self.HIGH_LATENCY_THRESHOLD
            and
            not self.latency_warning_sent
        ):

            logger.warning(
                f"Raspberry Pi A (WiFi) High Latency "
                f"({state.wifi.latency:.2f} ms)"
            )

            self.latency_warning_sent = True

        # Connection Timeout

        if (
            state.wifi.last_packet_age
            > self.WINDOW_SIZE
        ):

            state.wifi.connected = False

            if not self.timeout_warning_sent:

                logger.warning(
                    "Raspberry Pi A (WiFi) Connection Timeout"
                )

                self.timeout_warning_sent = True


wifi_statistics = WiFiStatistics()
