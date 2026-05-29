import { mqttClient } from '../config/mqtt.js';
import { ALL_TOPICS, parseNodeId, getTopicType } from './topics.js';
import { handleSensorData } from './handlers/sensorData.js';
import { handleStatusData } from './handlers/statusData.js';
import { handleImageData } from './handlers/imageData.js';
import { logger } from '../utils/logger.js';

export async function startMqttSubscriber() {
    const client = mqttClient;

    // Subscribe to all topics
    await new Promise((resolve, reject) => {
        client.subscribe(ALL_TOPICS, { qos: 1 }, (err) => {
            if (err) return reject(err);
            logger.info(`[MQTT Subscriber] Subscribed to: ${ALL_TOPICS.join(', ')}`);
            resolve();
        });
    });

    // Route incoming messages
    client.on('message', async (topic, message) => {
        const nodeId = parseNodeId(topic);
        const type = getTopicType(topic);

        if (!nodeId) {
            logger.warn(`[MQTT] Unrecognized topic: ${topic}`);
            return;
        }

        let payload;
        try {
            payload = JSON.parse(message.toString());
        } catch {
            // Image topics can be raw base64 strings
            payload = message.toString();
        }

        logger.debug(`[MQTT] ← ${topic} | nodeId=${nodeId} | type=${type}`);

        switch (type) {
            case 'sensor':
                await handleSensorData(nodeId, payload).catch((err) =>
                    logger.error(`[MQTT] sensorData handler error (${nodeId}):`, err.message)
                );
                break;

            case 'status':
                await handleStatusData(nodeId, payload).catch((err) =>
                    logger.error(`[MQTT] statusData handler error (${nodeId}):`, err.message)
                );
                break;

            case 'image':
                await handleImageData(nodeId, payload).catch((err) =>
                    logger.error(`[MQTT] imageData handler error (${nodeId}):`, err.message)
                );
                break;

            default:
                logger.warn(`[MQTT] Unknown topic type '${type}' from ${nodeId}`);
        }
    });

    logger.info('[MQTT Subscriber] Ready and listening');
}
