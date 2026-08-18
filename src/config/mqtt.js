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

        // Handler error KHUSUS fase connect awal. WAJIB dilepas begitu connect
        // sukses — kalau nyangkut, error runtime pertama (mis. broker drop) bakal
        // manggil client.end(true) & mqttClient=null → auto-reconnect mati permanen.
        const onInitError = (err) => {
            clearTimeout(timeout);
            client.removeListener('connect', onConnect);
            client.end(true);
            mqttClient = null;
            reject(err);
        };
        const onConnect = () => {
            clearTimeout(timeout);
            client.removeListener('error', onInitError); // lepas: biarin lib auto-reconnect kalau nanti drop
            resolve();
        };

        const timeout = setTimeout(() => {
            client.removeListener('connect', onConnect);
            client.removeListener('error', onInitError);
            client.end(true);
            mqttClient = null;
            reject(new Error(`MQTT connection timeout — broker at ${env.MQTT_BROKER_URL} unreachable`));
        }, 10_000);

        client.once('connect', onConnect);
        client.once('error', onInitError);
    });
    return client;
}

export { mqttClient };
