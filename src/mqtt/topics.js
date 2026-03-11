/**
 * MQTT Topic patterns and helper utilities
 */

export const TOPICS = {
    SENSOR: 'smartbin/+/sensor',
    STATUS: 'smartbin/+/status',
    IMAGE: 'smartbin/+/image',
};

export const ALL_TOPICS = Object.values(TOPICS);

/**
 * Parse nodeId from a topic string
 * e.g. "smartbin/bin-001/sensor" → "bin-001"
 * @param {string} topic
 * @returns {string|null}
 */
export function parseNodeId(topic) {
    const parts = topic.split('/');
    // topic format: smartbin/{nodeId}/{type}
    if (parts.length === 3 && parts[0] === 'smartbin') {
        return parts[1];
    }
    return null;
}

/**
 * Determine topic type from topic string
 * @param {string} topic
 * @returns {'sensor'|'status'|'image'|'unknown'}
 */
export function getTopicType(topic) {
    const parts = topic.split('/');
    if (parts.length !== 3) return 'unknown';
    return parts[2] || 'unknown';
}
