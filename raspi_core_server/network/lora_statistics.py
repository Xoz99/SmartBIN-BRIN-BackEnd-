from dashboard.state import state


class LoRaStatistics:

    def process_packet(
        self,
        payload
    ):

        state.lora.connected = True

        state.lora.packets += 1
        state.lora.received += 1

        state.lora.last_sequence = payload.get(
            "seq",
            state.lora.last_sequence
        )

        state.lora.rssi = payload.get(
            "rssi",
            state.lora.rssi
        )

        state.lora.snr = payload.get(
            "snr",
            state.lora.snr
        )


lora_statistics = LoRaStatistics()
