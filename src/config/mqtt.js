import mqtt from 'mqtt';
import { env } from './env.js';
import { logger } from '../utils/logger.js';

let mqttClient;

export function createMqttClient() {
    if (mqttClient) return mqttClient;

    const options = {
        clientId: env.MQTT_CLIENT_ID,
        clean: true,
        reconnectPeriod: 3000,
        connectTimeout: 10_000,
        keepalive: 60,
    };

    if (env.MQTT_USERNAME) {
        options.username = env.MQTT_USERNAME;
        options.password = env.MQTT_PASSWORD;
    }

    mqttClient = mqtt.connect(env.MQTT_BROKER_URL, options);

    mqttClient.on('connect', () => {
        logger.info(`[MQTT] Connected to broker: ${env.MQTT_BROKER_URL}`);
    });

    mqttClient.on('reconnect', () => {
        logger.warn('[MQTT] Reconnecting to broker...');
    });

    mqttClient.on('offline', () => {
        logger.warn('[MQTT] Client is offline');
    });

    mqttClient.on('error', (err) => {
        logger.error('[MQTT] Error:', err.message);
    });

    mqttClient.on('close', () => {
        logger.warn('[MQTT] Connection closed');
    });

    return mqttClient;
}

export async function connectMqtt() {
    const client = createMqttClient();
    await new Promise((resolve, reject) => {
        if (client.connected) return resolve();
        client.once('connect', resolve);
        client.once('error', reject);
    });
    return client;
}

export { mqttClient };
