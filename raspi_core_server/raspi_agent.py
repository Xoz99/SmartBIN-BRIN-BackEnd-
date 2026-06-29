"""
====================================================
Raspi Agent - System Metrics Publisher
====================================================
Jalanin ini di Raspberry Pi A.
Publish CPU, RAM, Disk, Suhu, Uptime, IP via MQTT.
====================================================
"""

import json
import socket
import time

import psutil
import paho.mqtt.client as mqtt

# ==================================================
# CONFIG - sesuaikan
# ==================================================

BROKER      = "100.99.74.71"   # Tailscale IP Raspi (diri sendiri)
PORT        = 1883
TOPIC       = "raspi/system"
CLIENT_ID   = "raspi_agent"
INTERVAL    = 2                 # detik

# ==================================================

def get_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000
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

    client = mqtt.Client(client_id=CLIENT_ID)
    client.connect(BROKER, PORT, 60)
    client.loop_start()

    print(f"[Agent] Connected to {BROKER}, publishing to '{TOPIC}' every {INTERVAL}s")

    while True:

        payload = {
            "cpu":    psutil.cpu_percent(interval=None),
            "ram":    psutil.virtual_memory().percent,
            "disk":   psutil.disk_usage("/").percent,
            "temp":   get_temp(),
            "uptime": get_uptime(),
            "ip":     get_ip(),
        }

        client.publish(TOPIC, json.dumps(payload))

        print(f"[Agent] Published: {payload}")

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
