"""
====================================================
Raspi Agent - System Metrics Publisher
====================================================
Jalanin ini di Raspberry Pi A.
Publish CPU, RAM, Disk, Suhu, Cache, Uptime, IP via MQTT.
====================================================
"""

import json
import socket
import ssl
import time

import psutil
import paho.mqtt.client as mqtt

# ==================================================
# CONFIG
# ==================================================

BROKER    = "4b1ed76fd60640648c995b6c90f11829.s1.eu.hivemq.cloud"
PORT      = 8883
TOPIC     = "raspi/system"
CLIENT_ID = "raspi_agent"
USERNAME  = "bintrash"
PASSWORD  = "Smartbinbrin1"
INTERVAL  = 2

# ==================================================

def get_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000
    except Exception:
        return 0.0


def get_cache_mb():
    try:
        mem = psutil.virtual_memory()
        return round(mem.cached / (1024 * 1024), 1)
    except Exception:
        return 0.0


def get_uptime():
    uptime_seconds = int(time.time() - psutil.boot_time())
    hours   = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "Unknown"


def main():
    client = mqtt.Client(
        client_id=CLIENT_ID,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
    )
    client.username_pw_set(USERNAME, PASSWORD)
    client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)
    client.connect(BROKER, PORT, 60)
    client.loop_start()

    print(f"[Agent] Connected to {BROKER}")
    print(f"[Agent] Publishing to '{TOPIC}' every {INTERVAL}s")

    while True:
        payload = {
            "cpu":    psutil.cpu_percent(interval=None),
            "ram":    psutil.virtual_memory().percent,
            "disk":   psutil.disk_usage("/").percent,
            "temp":   get_temp(),
            "cache":  get_cache_mb(),
            "uptime": get_uptime(),
            "ip":     get_ip(),
        }
        client.publish(TOPIC, json.dumps(payload))
        print(f"[Agent] Published: {payload}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()