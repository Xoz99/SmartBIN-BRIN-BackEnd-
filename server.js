import 'express-async-errors';
import { env } from './src/config/env.js';
import { connectDB } from './src/config/db.js';
import { connectRedis } from './src/config/redis.js';
import { connectMqtt } from './src/config/mqtt.js';
import { createApp } from './src/api/index.js';
import { initWebSocket } from './src/websocket/ws.js';
import { startMqttSubscriber } from './src/mqtt/subscriber.js';
import { logger } from './src/utils/logger.js';
import http from 'http';

let server;

async function bootstrap() {
    try {
        // 1. Database
        logger.info('[Bootstrap] Connecting to PostgreSQL...');
        await connectDB();

        // 2. Redis
        logger.info('[Bootstrap] Connecting to Redis...');
        await connectRedis();

        // 3. MQTT (optional — server continues without it)
        logger.info('[Bootstrap] Connecting to MQTT broker...');
        try {
            await connectMqtt();

            // 6. MQTT Subscriber (only if MQTT connected)
            logger.info('[Bootstrap] Starting MQTT subscriber...');
            await startMqttSubscriber();
        } catch (mqttErr) {
            logger.warn(`[Bootstrap] ⚠️ MQTT unavailable: ${mqttErr.message || 'connection failed'}. Server running without MQTT.`);
        }

        // 4. Express
        logger.info('[Bootstrap] Starting Express...');
        const app = createApp();
        server = http.createServer(app);

        // 5. WebSocket
        logger.info('[Bootstrap] Initializing WebSocket server...');
        initWebSocket(server);

        // Start listening
        server.listen(env.PORT, () => {
            logger.info(`[Bootstrap] 🚀 SmartBin Backend running on port ${env.PORT} (${env.NODE_ENV})`);
        });

    } catch (err) {
        logger.error('[Bootstrap] Startup failed:', err.message);
        process.exit(1);
    }
}

// ─── Graceful shutdown ────────────────────────────────────────────────────────
async function shutdown(signal) {
    logger.warn(`[Shutdown] Received ${signal}. Closing server gracefully...`);

    if (server) {
        server.close(() => {
            logger.info('[Shutdown] HTTP server closed');
        });
    }

    try {
        const { prisma } = await import('./src/config/db.js');
        await prisma.$disconnect();
        logger.info('[Shutdown] DB disconnected');
    } catch (_) { }

    try {
        const { redisClient } = await import('./src/config/redis.js');
        if (redisClient) await redisClient.quit();
        logger.info('[Shutdown] Redis disconnected');
    } catch (_) { }

    try {
        const { mqttClient } = await import('./src/config/mqtt.js');
        if (mqttClient) mqttClient.end();
        logger.info('[Shutdown] MQTT disconnected');
    } catch (_) { }

    process.exit(0);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('uncaughtException', (err) => {
    logger.error('[UncaughtException]', err);
    shutdown('uncaughtException');
});
process.on('unhandledRejection', (reason) => {
    logger.error('[UnhandledRejection]', reason);
});

bootstrap();
