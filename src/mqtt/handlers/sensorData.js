import { z } from 'zod';
import { prisma } from '../../config/db.js';
import { redisClient } from '../../config/redis.js';
import { checkThreshold } from '../../services/alert.service.js';
import { broadcast } from '../../websocket/ws.js';
import { logger } from '../../utils/logger.js';

// Zod schema for sensor payload validation
const SensorPayloadSchema = z.object({
    weight: z.number().min(0).max(200),     // kg
    volume: z.number().min(0).max(100),     // %
    battery: z.number().min(0).max(100),    // %
    gas: z.number().min(0).optional(),      // ppm — gas sensor (MQ-x)
    rssi: z.number().int().optional().default(-999),
});

/**
 * Handle incoming sensor data from a bin node
 * @param {string} nodeId
 * @param {object} rawPayload
 */
export async function handleSensorData(nodeId, rawPayload) {
    // 1. Validate payload
    const parsed = SensorPayloadSchema.safeParse(rawPayload);
    if (!parsed.success) {
        logger.warn(`[SensorHandler] Invalid payload from ${nodeId}:`, parsed.error.flatten());
        return;
    }
    const data = parsed.data;

    // 2. Find bin by nodeId
    const bin = await prisma.bin.findUnique({ where: { nodeId } });
    if (!bin) {
        logger.warn(`[SensorHandler] Unknown nodeId: ${nodeId}. Data discarded.`);
        return;
    }

    // 3. Save SensorLog to PostgreSQL
    const log = await prisma.sensorLog.create({
        data: {
            binId: bin.id,
            weight: data.weight,
            volume: data.volume,
            battery: data.battery,
            gas: data.gas ?? null,
            rssi: data.rssi,
        },
    });

    // 4. Cache latest reading in Redis (TTL: 1 hour)
    const cacheKey = `bin:${nodeId}:latest`;
    await redisClient.set(
        cacheKey,
        JSON.stringify({ ...data, timestamp: log.createdAt, logId: log.id }),
        'EX',
        3600
    );

    // 5. Check thresholds and trigger alerts if needed
    await checkThreshold(nodeId, bin.id, data);

    // 6. Broadcast to WebSocket clients
    broadcast('BIN_UPDATE', {
        nodeId,
        binId: bin.id,
        weight: data.weight,
        volume: data.volume,
        battery: data.battery,
        gas: data.gas ?? null,
        rssi: data.rssi,
        timestamp: log.createdAt,
    });

    logger.debug(`[SensorHandler] ✓ Saved log for ${nodeId} | w=${data.weight}kg v=${data.volume}% b=${data.battery}% g=${data.gas ?? '-'}ppm`);
}

