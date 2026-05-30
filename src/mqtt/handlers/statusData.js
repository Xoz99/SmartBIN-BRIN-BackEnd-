import { prisma } from '../../config/db.js';
import { redisClient } from '../../config/redis.js';
import { broadcast } from '../../websocket/ws.js';
import { logger } from '../../utils/logger.js';

/**
 * Handle online/offline heartbeat from a bin node
 * @param {string} nodeId
 * @param {{ status: 'online'|'offline' }} payload
 */
export async function handleStatusData(nodeId, payload) {
    const status = payload?.status;

    if (!['online', 'offline'].includes(status)) {
        logger.warn(`[StatusHandler] Invalid status payload from ${nodeId}:`, payload);
        return;
    }

    const bin = await prisma.bin.findUnique({ where: { nodeId }, select: { id: true } });
    if (!bin) {
        logger.warn(`[StatusHandler] Unknown nodeId: ${nodeId}. Status discarded.`);
        return;
    }

    const statusKey = `bin:${nodeId}:status`;
    const lastSeenKey = `bin:${nodeId}:lastSeen`;
    const now = new Date().toISOString();

    // Store status in Redis (TTL: 3 minutes — if no heartbeat arrives, assumed offline)
    await redisClient.set(statusKey, status, 'EX', 180);
    await redisClient.set(lastSeenKey, now, 'EX', 86400); // 24h

    // Broadcast to WebSocket clients
    await broadcast('BIN_STATUS', { nodeId, status, lastSeen: now });

    logger.debug(`[StatusHandler] ${nodeId} → ${status}`);
}
