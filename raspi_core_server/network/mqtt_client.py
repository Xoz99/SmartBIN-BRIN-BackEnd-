import json
import ssl

import paho.mqtt.client as mqtt

from config import (
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_TOPIC,
    MQTT_CLIENT_ID,
    MQTT_KEEPALIVE,
    MQTT_USERNAME,
    MQTT_PASSWORD,
)

from dashboard.state import state
from logger.log_manager import logger
from network.wifi import wifi_manager


class MQTTClient:

    def __init__(self):
        self.client = mqtt.Client(
            client_id=MQTT_CLIENT_ID,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2
        )
        self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        self.client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)
        self.client.on_connect    = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message    = self.on_message

    # ==================================================

    def connect(self):
        try:
            self.client.connect(MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE)
            self.client.loop_start()
        except Exception as e:
            logger.error(f"MQTT Connection Failed : {e}")

    # ==================================================

    def on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            state.mqtt.connected = True
            state.mqtt.broker    = MQTT_BROKER
            state.mqtt.topic     = MQTT_TOPIC

            client.subscribe("smartbin/#")   # semua node + semua subtopic
            client.subscribe("raspi/system")

            logger.success("MQTT Connected")
            logger.info("Subscribed : smartbin/#")
            logger.info("Subscribed : raspi/system")
        else:
            state.mqtt.connected = False
            logger.error(f"MQTT Connect Failed (rc={rc})")

    # ==================================================

    def on_disconnect(self, client, userdata, rc, properties=None):
        state.mqtt.connected = False
        logger.warning("MQTT Disconnected")

    # ==================================================

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())

            if msg.topic == "raspi/system":
                self.handle_system(payload)

            elif msg.topic.startswith("smartbin/"):
                self.handle_smartbin(msg.topic, payload, len(msg.payload))

        except json.JSONDecodeError:
            logger.error("Invalid JSON payload")
        except Exception as e:
            logger.error(str(e))

    # ==================================================

    def handle_system(self, payload):
        state.system.cpu    = payload.get("cpu",    0.0)
        state.system.ram    = payload.get("ram",    0.0)
        state.system.disk   = payload.get("disk",   0.0)
        state.system.temp   = payload.get("temp",   0.0)
        state.system.uptime = payload.get("uptime", "-")
        state.system.ip     = payload.get("ip",     "-")

    # ==================================================

    def handle_smartbin(self, topic, payload, payload_size):
        parts   = topic.split("/")
        node_id = parts[1] if len(parts) > 1 else "unknown"

        if "status" in topic:
            status = payload.get("status", "unknown")
            logger.info(f"SmartBIN [{node_id}] Status: {status.upper()}")

        elif "sensor" in topic:
            weight   = payload.get("weight",   "-")
            volume   = payload.get("volume",   "-")
            battery  = payload.get("battery",  "-")
            gas      = payload.get("gas",      "-")
            distance = payload.get("distance", "-")
            rssi     = payload.get("rssi",     "-")
            lat      = payload.get("lat",      0.0)
            lng      = payload.get("lng",      0.0)

            logger.info(
                f"SmartBIN [{node_id}] "
                f"Weight={weight}kg | Volume={volume}% | "
                f"Battery={battery}% | Gas={gas}ppm | "
                f"Distance={distance}cm | RSSI={rssi}dBm"
            )
            logger.info(
                f"SmartBIN [{node_id}] "
                f"Location=({lat:.6f}, {lng:.6f})"
            )

            wifi_manager.process_payload(payload, payload_size)


mqtt_client = MQTTClient()