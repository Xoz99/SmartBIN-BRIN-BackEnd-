import Redis from 'ioredis';
import { env } from './env.js';
import { logger } from '../utils/logger.js';

let redisClient;

export function createRedisClient() {
    if (redisClient) return redisClient;

    redisClient = new Redis(env.REDIS_URL, {
        maxRetriesPerRequest: null,
        enableReadyCheck: true,
        retryStrategy(times) {
            const delay = Math.min(times * 500, 5000);
            logger.warn(`[Redis] Reconnecting... attempt ${times}, next retry in ${delay}ms`);
            return delay;
        },
    });

    redisClient.on('connect', () => logger.info('[Redis] Connecting...'));
    redisClient.on('ready', () => logger.info('[Redis] Connected and ready'));
    redisClient.on('error', (err) => logger.error('[Redis] Error:', err.message));
    redisClient.on('close', () => logger.warn('[Redis] Connection closed'));
    redisClient.on('reconnecting', () => logger.warn('[Redis] Reconnecting...'));

    return redisClient;
}

export async function connectRedis() {
    const client = createRedisClient();
    // Wait for ready state
    await new Promise((resolve, reject) => {
        if (client.status === 'ready') return resolve();
        client.once('ready', resolve);
        client.once('error', reject);
    });
    logger.info('[Redis] Redis client ready');
    return client;
}

export { redisClient };
