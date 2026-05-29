import { findAllBins, findBinById, findBinByNodeId, updateBin, findFullBins } from '../models/bin.model.js';
import { findLogsByBinId, findLatestByBinId } from '../models/sensorLog.model.js';
import { redisClient } from '../config/redis.js';
import { env } from '../config/env.js';
import { logger } from '../utils/logger.js';
import QRCode from 'qrcode';

/**
 * Get all bins enriched with live Redis status and latest sensor data
 * @param {object} user - The user requesting the bins
 */
async function safeRedisGet(key) {
    try {
        if (!redisClient || redisClient.status !== 'ready') return null;
        return await redisClient.get(key);
    } catch {
        return null;
    }
}

export async function getAllBins(user) {
    const bins = await findAllBins(user);

    const enriched = await Promise.all(
        bins.map(async (bin) => {
            const [latest, status, lastSeen] = await Promise.all([
                safeRedisGet(`bin:${bin.nodeId}:latest`),
                safeRedisGet(`bin:${bin.nodeId}:status`),
                safeRedisGet(`bin:${bin.nodeId}:lastSeen`),
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
        safeRedisGet(`bin:${bin.nodeId}:latest`),
        safeRedisGet(`bin:${bin.nodeId}:status`),
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
 * Set alert thresholds for a bin — persisted to DB
 * @param {string} nodeId
 * @param {{ weightThreshold?: number, volumeThreshold?: number, gasThreshold?: number, batteryThreshold?: number }} thresholds
 */
export async function setThreshold(nodeId, { weightThreshold, volumeThreshold, gasThreshold, batteryThreshold }) {
    const bin = await findBinByNodeId(nodeId);
    if (!bin) throw Object.assign(new Error('Bin not found'), { statusCode: 404 });

    const data = {};
    if (weightThreshold !== undefined) data.weightThreshold = weightThreshold;
    if (volumeThreshold !== undefined) data.volumeThreshold = volumeThreshold;
    if (gasThreshold !== undefined) data.gasThreshold = gasThreshold;
    if (batteryThreshold !== undefined) data.batteryThreshold = batteryThreshold;

    const updatedBin = await updateBin(bin.id, data);
    logger.info(`[BinService] Threshold updated for ${nodeId}:`, data);

    return {
        weight: updatedBin.weightThreshold ?? env.DEFAULT_WEIGHT_THRESHOLD,
        volume: updatedBin.volumeThreshold ?? env.DEFAULT_VOLUME_THRESHOLD,
        gas: updatedBin.gasThreshold ?? env.DEFAULT_GAS_THRESHOLD,
        battery: updatedBin.batteryThreshold ?? env.DEFAULT_BATTERY_THRESHOLD,
    };
}

/**
 * Get current threshold for a bin — reads DB, falls back to env defaults
 * @param {string} nodeId
 */
export async function getBinThreshold(nodeId) {
    const bin = await findBinByNodeId(nodeId);
    return {
        weight: bin?.weightThreshold ?? env.DEFAULT_WEIGHT_THRESHOLD,
        volume: bin?.volumeThreshold ?? env.DEFAULT_VOLUME_THRESHOLD,
        gas: bin?.gasThreshold ?? env.DEFAULT_GAS_THRESHOLD,
        battery: bin?.batteryThreshold ?? env.DEFAULT_BATTERY_THRESHOLD,
    };
}

function haversine(lat1, lon1, lat2, lon2) {
    const R = 6371; // Radius of Earth in km
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon / 2) * Math.sin(dLon / 2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
}

/**
 * Get the most optimal route for full bins based on starting coordinates
 */
export async function getOptimalRoute(originLat, originLng, user) {
    const fullBins = await findFullBins(user);
    if (!fullBins || fullBins.length === 0) {
        return { route: [], googleMapsUrl: null, message: "Bagus! Tidak ada tempat sampah yang penuh." };
    }

    // Greedy TSP - Nearest Neighbor
    let currentLat = originLat;
    let currentLng = originLng;
    let unvisited = [...fullBins];
    const route = [];

    while (unvisited.length > 0) {
        let nearestIdx = 0;
        let minDistance = Infinity;

        for (let i = 0; i < unvisited.length; i++) {
            const dist = haversine(currentLat, currentLng, unvisited[i].lat, unvisited[i].lng);
            if (dist < minDistance) {
                minDistance = dist;
                nearestIdx = i;
            }
        }

        const nextBin = unvisited.splice(nearestIdx, 1)[0];
        nextBin.distanceFromPreviousKm = Number(minDistance.toFixed(2));
        route.push(nextBin);
        
        currentLat = nextBin.lat;
        currentLng = nextBin.lng;
    }

    const destination = route[route.length - 1];
    let googleMapsUrl = `https://www.google.com/maps/dir/?api=1&origin=${originLat},${originLng}&destination=${destination.lat},${destination.lng}&travelmode=driving`;
    
    if (route.length > 1) {
        // Exclude the last bin from waypoints, as it's the destination
        const waypoints = route.slice(0, route.length - 1).map(b => `${b.lat},${b.lng}`).join('|');
        googleMapsUrl += `&waypoints=${waypoints}`;
    }

    let qrCodeBase64 = null;
    if (googleMapsUrl) {
        qrCodeBase64 = await QRCode.toDataURL(googleMapsUrl, {
            width: 400,
            margin: 2,
            color: {
                dark: '#000000',
                light: '#ffffff'
            }
        });
    }

    return { route, googleMapsUrl, qrCodeBase64 };
}
