/**
 * One-shot MQTT sensor trigger ke broker Mosquitto lokal.
 *
 * Nembak SATU payload sensor ke broker (default 192.168.0.101) lalu keluar.
 * Beda sama simulate-mqtt.js yg loop terus — ini sekali tembak buat ngetes cepat.
 *
 * Topic   : smartbin/{nodeId}/sensor   (lihat src/mqtt/topics.js)
 * Payload : { weight, volume, battery, gas, distance, lat, lng, rssi }
 *           semua field opsional — cocok dengan SensorPayloadSchema di handler.
 *
 * Contoh:
 *   node scripts/trigger-sensor-data.js
 *   node scripts/trigger-sensor-data.js bin-001 --weight 12.5 --volume 80
 *   node scripts/trigger-sensor-data.js bin-002 --distance 8 --battery 95 --gas 120
 *   node scripts/trigger-sensor-data.js bin-003 --broker mqtt://192.168.0.101:1883
 *
 * Flag: --weight --volume --battery --gas --distance --lat --lng --rssi --broker
 */

import mqtt from 'mqtt';
import { config } from 'dotenv';

config();

// Broker default → IP lokal Mosquitto. Override lewat env MQTT_BROKER_URL atau flag --broker.
const DEFAULT_BROKER = 'mqtt://192.168.0.101:1883';

// --- Parse argumen CLI ---------------------------------------------------
const args = process.argv.slice(2);
const NUMERIC_FLAGS = ['weight', 'volume', 'battery', 'gas', 'distance', 'lat', 'lng', 'rssi'];

let nodeId = 'bin-001';
let brokerUrl = process.env.MQTT_BROKER_URL || DEFAULT_BROKER;
const payload = {};

for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '--broker') {
        brokerUrl = args[++i];
    } else if (a.startsWith('--')) {
        const key = a.slice(2);
        const val = args[++i];
        if (NUMERIC_FLAGS.includes(key)) {
            payload[key] = Number(val);
        } else {
            console.warn(`[trigger] flag tidak dikenal: ${a} (diabaikan)`);
        }
    } else {
        // argumen posisional pertama = nodeId
        nodeId = a;
    }
}

// Kalau user nggak kasih field apa pun, isi nilai contoh yg masuk akal.
if (Object.keys(payload).length === 0) {
    payload.weight = 12.5;
    payload.volume = 75;
    payload.battery = 90;
    payload.distance = 12;
    payload.rssi = -65;
}

const topic = `smartbin/${nodeId}/sensor`;

// --- Connect & publish ---------------------------------------------------
const client = mqtt.connect(brokerUrl, {
    clientId: `smartbin-trigger-${Date.now()}`,
    clean: true,
    // Mosquitto lokal biasanya tanpa auth; kalau pakai, set MQTT_USERNAME/PASSWORD di .env.
    username: process.env.MQTT_USERNAME || undefined,
    password: process.env.MQTT_PASSWORD || undefined,
    connectTimeout: 8000,
});

client.on('connect', () => {
    console.log(`[trigger] Connected → ${brokerUrl}`);
    client.publish(topic, JSON.stringify(payload), { qos: 1 }, (err) => {
        if (err) {
            console.error('[trigger] Publish gagal:', err.message);
            client.end(() => process.exit(1));
            return;
        }
        console.log(`[trigger] ✓ Published → ${topic}`);
        console.log('[trigger] payload:', payload);
        client.end(() => process.exit(0));
    });
});

client.on('error', (err) => {
    console.error('[trigger] MQTT error:', err.message);
    client.end(() => process.exit(1));
});

// Safety net: kalau broker nggak respon, jangan gantung selamanya.
setTimeout(() => {
    console.error('[trigger] Timeout — broker tidak merespon.');
    client.end(() => process.exit(1));
}, 10000);
