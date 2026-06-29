import json

import paho.mqtt.client as mqtt

from config import (
    MQTT_BROKER,
    MQTT_PORT,
    MQTT_TOPIC,
    MQTT_CLIENT_ID,
    MQTT_KEEPALIVE,
)

from dashboard.state import state
from logger.log_manager import logger
from network.wifi import wifi_manager


class MQTTClient:

    def __init__(self):
        self.client = mqtt.Client(client_id=MQTT_CLIENT_ID)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    def connect(self):
        try:
            self.client.connect(MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE)
            self.client.loop_start()
        except Exception as e:
            logger.error(f"MQTT Connection Failed : {e}")

    def on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            state.mqtt.connected = True
            state.mqtt.broker = MQTT_BROKER
            state.mqtt.topic = MQTT_TOPIC

            client.subscribe(MQTT_TOPIC)
            client.subscribe("raspi/system")   # <-- tambahan

            logger.success("MQTT Connected")
            logger.info(f"Subscribed : {MQTT_TOPIC}")
            logger.info("Subscribed : raspi/system")
        else:
            state.mqtt.connected = False
            logger.error(f"MQTT Connect Failed ({rc})")

    def on_disconnect(self, client, userdata, rc, properties=None):
        state.mqtt.connected = False
        logger.warning("MQTT Disconnected")

    def on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())

            if msg.topic == "raspi/system":
                self.handle_system(payload)
            elif msg.topic == MQTT_TOPIC:
                wifi_manager.process_payload(payload, len(msg.payload))

        except json.JSONDecodeError:
            logger.error("Invalid JSON payload")
        except Exception as e:
            logger.error(str(e))

    def handle_system(self, payload):
        state.system.cpu    = payload.get("cpu",    0.0)
        state.system.ram    = payload.get("ram",    0.0)
        state.system.disk   = payload.get("disk",   0.0)
        state.system.temp   = payload.get("temp",   0.0)
        state.system.uptime = payload.get("uptime", "-")
        state.system.ip     = payload.get("ip",     "-")


mqtt_client = MQTTClient()