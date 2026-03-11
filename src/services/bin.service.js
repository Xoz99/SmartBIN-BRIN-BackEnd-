import { findAllBins, findBinById } from '../models/bin.model.js';
import { findLogsByBinId, findLatestByBinId } from '../models/sensorLog.model.js';
import { redisClient } from '../config/redis.js';
import { env } from '../config/env.js';
import { logger } from '../utils/logger.js';

/**
 * Get all bins enriched with live Redis status and latest sensor data
 * @param {object} user - The user requesting the bins
 */
export async function getAllBins(user) {
    const bins = await findAllBins(user);

    const enriched = await Promise.all(
        bins.map(async (bin) => {
            const [latest, status, lastSeen] = await Promise.all([
                redisClient.get(`bin:${bin.nodeId}:latest`),
                redisClient.get(`bin:${bin.nodeId}:status`),
                redisClient.get(`bin:${bin.nodeId}:lastSeen`),
            ]);
            return {
                ...bin,
                status: status || 'offline',
                lastSeen: lastSeen || null,
                latest: latest ? JSON.parse(latest) : null,
            };
        })
    );

    return enriched;
}

/**
 * Get a single bin with full details
 * @param {string} id
 */
export async function getBinById(id) {
    const bin = await findBinById(id);
    if (!bin) return null;

    const [latest, status] = await Promise.all([
        redisClient.get(`bin:${bin.nodeId}:latest`),
        redisClient.get(`bin:${bin.nodeId}:status`),
    ]);

    const threshold = await getBinThreshold(bin.nodeId);

    return {
        ...bin,
        status: status || 'offline',
        latest: latest ? JSON.parse(latest) : null,
        threshold,
    };
}

/**
 * Get paginated sensor history for a bin
 * @param {string} id - Bin primary key
 * @param {number} limit
 * @param {number} page
 */
export async function getBinHistory(id, limit = 50, page = 1) {
    return findLogsByBinId(id, limit, page);
}

/**
 * Set alert thresholds for a bin — stored in Redis
 * @param {string} nodeId
 * @param {{ weightThreshold?: number, volumeThreshold?: number }} thresholds
 */
export async function setThreshold(nodeId, { weightThreshold, volumeThreshold }) {
    const key = `bin:${nodeId}:threshold`;
    const existing = await getBinThreshold(nodeId);

    const updated = {
        weight: weightThreshold ?? existing.weight,
        volume: volumeThreshold ?? existing.volume,
    };

    await redisClient.set(key, JSON.stringify(updated));
    logger.info(`[BinService] Threshold updated for ${nodeId}:`, updated);
    return updated;
}

/**
 * Get current threshold for a bin (fallback to env defaults)
 * @param {string} nodeId
 */
export async function getBinThreshold(nodeId) {
    const raw = await redisClient.get(`bin:${nodeId}:threshold`);
    if (raw) return JSON.parse(raw);
    return {
        weight: env.DEFAULT_WEIGHT_THRESHOLD,
        volume: env.DEFAULT_VOLUME_THRESHOLD,
    };
}
