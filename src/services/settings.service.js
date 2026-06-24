import { redisClient } from '../config/redis.js';
import { logger } from '../utils/logger.js';

// Setelan operasional global sistem (disimpan di Redis — 1 angka, hemat RAM).
const KEY_PICKUP_THRESHOLD = 'settings:pickupThreshold';
const DEFAULT_PICKUP_THRESHOLD = 75; // % — ambang tong masuk "Perlu Diangkut"

/**
 * Ambil ambang "Perlu Diangkut" (%). Default 75 kalau belum diset / Redis mati.
 */
export async function getPickupThreshold() {
    if (!redisClient) return DEFAULT_PICKUP_THRESHOLD;
    try {
        const v = await redisClient.get(KEY_PICKUP_THRESHOLD);
        const n = v == null ? NaN : parseInt(v, 10);
        return Number.isFinite(n) ? n : DEFAULT_PICKUP_THRESHOLD;
    } catch {
        return DEFAULT_PICKUP_THRESHOLD;
    }
}

/**
 * Set ambang "Perlu Diangkut" (%). Clamp 50–95.
 */
export async function setPickupThreshold(value) {
    const n = Math.min(95, Math.max(50, Math.round(Number(value))));
    if (!Number.isFinite(n)) throw Object.assign(new Error('Nilai threshold tidak valid'), { statusCode: 400 });
    if (redisClient) {
        try { await redisClient.set(KEY_PICKUP_THRESHOLD, String(n)); }
        catch (e) { logger.warn(`[Settings] gagal simpan threshold: ${e.message}`); }
    }
    return n;
}

/** Ambil semua setelan (buat frontend). */
export async function getSettings() {
    return { pickupThreshold: await getPickupThreshold() };
}
