"""
====================================================
WiFi Manager
====================================================
Controller untuk seluruh data Raspberry Pi A (WiFi).
"""

import time

from dashboard.state import state
from logger.log_manager import logger
from network.wifi_statistics import wifi_statistics


class WiFiManager:

    def process_payload(self, payload, payload_size):

        receive_time = time.time()   # catat waktu terima di sisi Windows

        sequence  = payload.get("seq")
        timestamp = payload.get("timestamp")

        # ============================================
        # REQUEST
        # ============================================

        logger.request(
            f"Raspberry Pi A (WiFi) | Packet Received | "
            f"Seq={sequence} | Size={payload_size} B"
        )

        # ============================================
        # PROCESS TELEMETRY
        # ============================================

        wifi_statistics.process_packet(
            payload_size=payload_size,
            sequence=sequence,
            timestamp=timestamp,
            receive_time=receive_time,
        )

        # ============================================
        # POST
        # ============================================

        logger.post(
            f"Raspberry Pi A (WiFi) | "
            f"Telemetry Processed"
        )

        # ============================================
        # UPDATE
        # ============================================

        logger.update(
            "Raspberry Pi A (WiFi) | "
            f"Seq={state.wifi.last_sequence} | "
            f"Loss={state.wifi.loss} ({state.wifi.loss_percent:.2f}%) | "
            f"Rate={state.wifi.packet_rate:.2f} pkt/s | "
            f"Latency={state.wifi.latency:.2f} ms | "
            f"Throughput={state.wifi.throughput:.2f} KB/s"
        )


wifi_manager = WiFiManager()
