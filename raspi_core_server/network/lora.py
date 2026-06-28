from logger.log_manager import logger

from network.lora_statistics import (
    lora_statistics
)


class LoRaManager:

    def process_payload(
        self,
        payload
    ):

        sequence = payload.get("seq")

        logger.request(
            f"Raspberry Pi B (LoRa) | Packet Received | Seq={sequence}"
        )

        lora_statistics.process_packet(
            payload
        )

        logger.post(
            "Raspberry Pi B (LoRa) | Telemetry Processed"
        )

        logger.update(
            f"Raspberry Pi B (LoRa) | "
            f"Seq={sequence}"
        )


lora_manager = LoRaManager()
