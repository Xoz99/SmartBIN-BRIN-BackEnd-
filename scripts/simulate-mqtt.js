/**
 * MQTT sensor data simulator for local development / testing
 *
 * Publishes fake sensor data every 5 seconds for bin-001, bin-002, bin-003
 * Occasionally sends values that exceed thresholds to test alert generation.
 *
 * Usage: node scripts/simulate-mqtt.js
 */

import mqtt from 'mqtt';
import { config } from 'dotenv';

config();

const BROKER_URL = process.env.MQTT_BROKER_URL || 'mqtt://localhost:1883';
const NODES = ['bin-001', 'bin-002', 'bin-003'];
const INTERVAL_MS = 5000;

const client = mqtt.connect(BROKER_URL, {
    clientId: `smartbin-simulator-${Date.now()}`,
    clean: true,
});

// Internal state per node
const state = {
    'bin-001': { weight: 30, volume: 70, battery: 80 },
    'bin-002': { weight: 20, volume: 50, battery: 90 },
    'bin-003': { weight: 40, volume: 80, battery: 60 },
};

function lerp(current, target, factor = 0.2) {
    return current + (target - current) * factor;
}

function randomDrift(val, min, max, delta = 3) {
    const next = val + (Math.random() - 0.4) * delta;
    return Math.max(min, Math.min(max, next));
}

function publishSensorData(nodeId) {
    const s = state[nodeId];

    // Slowly increase weight and volume over time (simulates filling)
    s.weight = randomDrift(s.weight, 0, 55, 4);
    s.volume = randomDrift(s.volume, 0, 105, 5);
    s.battery = randomDrift(s.battery, 10, 100, 0.5);

    const payload = {
        weight: parseFloat(s.weight.toFixed(2)),
        volume: parseFloat(Math.min(s.volume, 100).toFixed(2)),
        battery: parseFloat(Math.min(s.battery, 100).toFixed(2)),
        rssi: -(Math.floor(60 + Math.random() * 30)),
    };

    const topic = `smartbin/${nodeId}/sensor`;
    client.publish(topic, JSON.stringify(payload), { qos: 1 });
    console.log(`[→] ${topic}`, payload);

    // Simulate "emptied" when weight goes over 50
    if (s.weight > 50) {
        console.log(`[!] ${nodeId} is FULL (${s.weight.toFixed(1)}kg) — resetting after dump simulation`);
        setTimeout(() => {
            s.weight = 2;
            s.volume = 5;
        }, 15000);
    }
}

function publishStatus(nodeId, status = 'online') {
    const topic = `smartbin/${nodeId}/status`;
    client.publish(topic, JSON.stringify({ status }), { qos: 0 });
}

client.on('connect', () => {
    console.log(`[MQTT Simulator] Connected to ${BROKER_URL}`);
    console.log(`[MQTT Simulator] Simulating nodes: ${NODES.join(', ')}`);
    console.log(`[MQTT Simulator] Publishing every ${INTERVAL_MS / 1000}s\n`);

    // Announce all nodes as online
    NODES.forEach((n) => publishStatus(n, 'online'));

    // Start publishing sensor data
    setInterval(() => {
        NODES.forEach(publishSensorData);
    }, INTERVAL_MS);
});

client.on('error', (err) => {
    console.error('[MQTT Simulator] Error:', err.message);
});

// Graceful shutdown
process.on('SIGINT', () => {
    console.log('\n[MQTT Simulator] Shutting down...');
    NODES.forEach((n) => publishStatus(n, 'offline'));
    setTimeout(() => { client.end(); process.exit(0); }, 500);
});
