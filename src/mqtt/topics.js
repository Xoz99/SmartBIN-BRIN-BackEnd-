/**
 * MQTT Topic patterns and helper utilities
 */

export const TOPICS = {
    SENSOR: 'smartbin/+/sensor',
    STATUS: 'smartbin/+/status',
    IMAGE: 'smartbin/+/image',
    CLASSIFICATION: 'smartbin/+/classification',

    // Remote control Raspi (main.py) — dinamespace di bawah `device/` supaya
    // TIDAK bentrok dengan `smartbin/+/cmd` yang dipakai perintah aktuator STM32.
    DEVICE_STATE: 'smartbin/+/device/state',
    DEVICE_ACK: 'smartbin/+/device/ack',
    DEVICE_LOG: 'smartbin/+/device/log',
};

export const ALL_TOPICS = Object.values(TOPICS);

/**
 * Topic perintah keluar ke ESP bin tertentu (mis. perintah pilah)
 * e.g. commandTopic("bin-001") → "smartbin/bin-001/command"
 * @param {string} nodeId
 */
export function commandTopic(nodeId) {
    return `smartbin/${nodeId}/command`;
}

/**
 * Topic perintah remote-control ke Raspi (didengar oleh remote_control.py).
 * e.g. deviceCommandTopic("bin-003") → "smartbin/bin-003/device/cmd"
 * @param {string} nodeId
 */
export function deviceCommandTopic(nodeId) {
    return `smartbin/${nodeId}/device/cmd`;
}

/**
 * Parse nodeId from a topic string
 * e.g. "smartbin/bin-001/sensor" → "bin-001"
 * @param {string} topic
 * @returns {string|null}
 */
export function parseNodeId(topic) {
    const parts = topic.split('/');
    // Format: smartbin/{nodeId}/{type} — atau smartbin/{nodeId}/device/{sub}
    if ((parts.length === 3 || parts.length === 4) && parts[0] === 'smartbin') {
        return parts[1] || null;
    }
    return null;
}

/**
 * Determine topic type from topic string
 * e.g. "smartbin/bin-001/sensor"       → "sensor"
 *      "smartbin/bin-001/device/state" → "device/state"
 * @param {string} topic
 * @returns {'sensor'|'status'|'image'|'classification'|'device/state'|'device/ack'|'device/log'|'unknown'}
 */
export function getTopicType(topic) {
    const parts = topic.split('/');
    if (parts.length === 3) return parts[2] || 'unknown';
    // Subtopik device dipertahankan utuh supaya router bisa membedakan
    // state/ack/log tanpa mem-parse ulang.
    if (parts.length === 4 && parts[2] === 'device') {
        return parts[3] ? `device/${parts[3]}` : 'unknown';
    }
    return 'unknown';
}
