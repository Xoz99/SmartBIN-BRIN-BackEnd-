import { randomUUID } from 'crypto';
import { mqttClient } from '../config/mqtt.js';
import { deviceCommandTopic } from '../mqtt/topics.js';
import { logger } from '../utils/logger.js';

/**
 * Remote control Raspi (main.py) lewat MQTT.
 *
 * Kenapa lewat broker, bukan HTTP langsung ke Pi: Pi duduk di belakang NAT
 * (WiFi kost/hotspot) — tidak punya IP publik dan tidak bisa di-port-forward.
 * Broker jadi rendezvous: Pi outbound ke HiveMQ, backend juga, keduanya
 * ketemu tanpa perlu saling menjangkau di level jaringan.
 *
 * Alur satu perintah:
 *   sendCommand() → publish device/cmd (qos 1) → daftar waiter di memori
 *   → Pi eksekusi → publish device/ack → handleDeviceAck() → waiter resolve
 */

const ACK_TIMEOUT_MS = Number(process.env.DEVICE_ACK_TIMEOUT_MS || 12_000);

/** id perintah → { resolve, timer }. Aman di memori: backend satu proses (server.js). */
const pending = new Map();

/** nodeId → state retained terakhir; dipakai isOnline() & GET /status. */
const lastState = new Map();

/** Perintah yang boleh dikirim. Whitelist — harus sama dengan action di remote_control.py. */
export const ALLOWED_ACTIONS = new Set([
    'status',
    'camera_start',
    'camera_stop',
    'actuator',
    'log_stream',
    'ping',
    'shutdown',
    'reboot',
]);

/**
 * Kirim perintah ke Pi dan tunggu ack-nya.
 * @param {string} nodeId
 * @param {string} action
 * @param {object} [args]
 * @returns {Promise<object>} isi ack dari Pi
 */
export function sendCommand(nodeId, action, args = {}) {
    if (!ALLOWED_ACTIONS.has(action)) {
        const err = new Error(`Action tidak diizinkan: ${action}`);
        err.statusCode = 400;
        throw err;
    }
    if (!mqttClient || !mqttClient.connected) {
        const err = new Error('Broker MQTT tidak terhubung — perintah tidak bisa dikirim');
        err.statusCode = 503;
        throw err;
    }

    const id = randomUUID();
    const topic = deviceCommandTopic(nodeId);
    const payload = JSON.stringify({ id, action, args });

    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
            pending.delete(id);
            const err = new Error(
                `Perangkat ${nodeId} tidak merespons dalam ${ACK_TIMEOUT_MS / 1000}s ` +
                `(perintah mungkin tetap dieksekusi — cek status)`
            );
            err.statusCode = 504;
            reject(err);
        }, ACK_TIMEOUT_MS);

        pending.set(id, { resolve, timer });

        // QoS 1: broker menyimpan perintah kalau Pi sedang reconnect.
        mqttClient.publish(topic, payload, { qos: 1 }, (err) => {
            if (!err) return;
            clearTimeout(timer);
            pending.delete(id);
            err.statusCode = 502;
            reject(err);
        });

        logger.debug(`[DeviceControl] → ${topic} | ${action} (id=${id})`);
    });
}

/** Dipanggil subscriber saat ack masuk dari Pi. */
export function handleDeviceAck(nodeId, payload) {
    const id = payload?.id;
    const waiter = id && pending.get(id);
    if (!waiter) {
        // Wajar: ack telat setelah timeout, atau ack dari instance backend lain.
        logger.debug(`[DeviceControl] ← ack tanpa waiter (${nodeId}, id=${id})`);
        return;
    }
    clearTimeout(waiter.timer);
    pending.delete(id);
    waiter.resolve(payload);
}

/** Dipanggil subscriber saat state retained masuk. */
export function setDeviceState(nodeId, payload) {
    lastState.set(nodeId, { ...payload, receivedAt: Date.now() });
}

/**
 * State terakhir yang diketahui. `null` kalau belum pernah dengar dari node ini
 * — bedakan dari `{online:false}` yang artinya node dipastikan mati.
 */
export function getDeviceState(nodeId) {
    return lastState.get(nodeId) || null;
}

export function getAllDeviceStates() {
    return Object.fromEntries(lastState);
}
