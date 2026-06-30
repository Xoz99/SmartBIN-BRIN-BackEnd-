import paho.mqtt.client as mqtt
import json
import time
import random

BROKER   = "4b1ed76fd60640648c995b6c90f11829.s1.eu.hivemq.cloud"
PORT     = 8883
NODE_ID  = "bin-003"
INTERVAL = 5

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"[+] Connected to MQTT broker {BROKER}:{PORT}")
        client.publish(f"smartbin/{NODE_ID}/status", json.dumps({"status": "online"}))
        print(f"[+] Status online published")
    else:
        print(f"[!] Failed to connect, rc={rc}")

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.username_pw_set("bintrash", "Smartbinbrin1")
client.tls_set()
client.on_connect = on_connect
client.connect(BROKER, PORT, keepalive=60)
client.loop_start()

print(f"[*] Simulating SmartBIN node: {NODE_ID}")
print(f"[*] Publishing every {INTERVAL}s — Ctrl+C to stop\n")

try:
    count = 0
    while True:
        count += 1
        payload = {
            "seq":       count,
            "timestamp": time.time(),
            "weight":    round(random.uniform(5, 40), 1),
            "volume":    round(random.uniform(10, 90), 1),
            "battery":   round(random.uniform(70, 100), 1),
            "gas":       round(random.uniform(100, 400), 1),
            "distance":  round(random.uniform(5, 30), 1),
            "lat":       -6.9018924110287845 + random.uniform(-0.0005, 0.0005),
            "lng":       107.58069498026289  + random.uniform(-0.0005, 0.0005),
            "rssi":      random.randint(-80, -50),
        }
        client.publish(f"smartbin/{NODE_ID}/sensor", json.dumps(payload))
        print(f"[{count}] Sent: {payload}")
        time.sleep(INTERVAL)

except KeyboardInterrupt:
    print("\n[*] Stopped.")
    client.publish(f"smartbin/{NODE_ID}/status", json.dumps({"status": "offline"}))
    client.loop_stop()
    client.disconnect()
