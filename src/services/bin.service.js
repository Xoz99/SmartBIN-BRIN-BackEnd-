import { findAllBins, findBinById, findBinByNodeId, updateBin } from '../models/bin.model.js';
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

const OSRM_BASE = 'https://router.project-osrm.org';

/**
 * Ambil duration matrix antar semua titik via OSRM /table
 * coords: array of { lat, lng }
 * Return: matrix NxN (detik)
 */
async function osrmTable(coords) {
    const coordStr = coords.map(c => `${c.lng},${c.lat}`).join(';');
    const url = `${OSRM_BASE}/table/v1/driving/${coordStr}?annotations=duration`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`OSRM table error: ${res.status}`);
    const data = await res.json();
    if (data.code !== 'Ok') throw new Error(`OSRM: ${data.message}`);
    return data.durations; // matrix[i][j] = detik dari i ke j
}

/**
 * Ambil polyline rute lengkap via OSRM /route
 * Return: { geometry (GeoJSON coords [[lon,lat]]), distance (m), duration (s) }
 */
async function osrmRoute(coords) {
    const coordStr = coords.map(c => `${c.lng},${c.lat}`).join(';');
    const url = `${OSRM_BASE}/route/v1/driving/${coordStr}?overview=full&geometries=geojson`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`OSRM route error: ${res.status}`);
    const data = await res.json();
    if (data.code !== 'Ok' || !data.routes?.length) throw new Error('OSRM: no route found');
    return {
        geometry:  data.routes[0].geometry.coordinates, // [[lon, lat], ...]
        distance:  data.routes[0].distance,
        duration:  data.routes[0].duration,
    };
}

/**
 * Greedy TSP Nearest Neighbor menggunakan duration matrix OSRM
 * nodes: array index (0 = origin, 1..n = bins)
 * matrix: NxN duration matrix
 * Return: urutan index yang optimal
 */
function tspNearestNeighbor(matrix) {
    const n = matrix.length;
    const visited = new Array(n).fill(false);
    const order = [0];
    visited[0] = true;

    for (let step = 1; step < n; step++) {
        const last = order[order.length - 1];
        let nearest = -1, minDur = Infinity;
        for (let j = 1; j < n; j++) { // skip 0 (origin)
            if (!visited[j] && matrix[last][j] < minDur) {
                minDur = matrix[last][j];
                nearest = j;
            }
        }
        if (nearest === -1) break;
        visited[nearest] = true;
        order.push(nearest);
    }
    return order;
}

/**
 * Get the most optimal route for full bins based on starting coordinates
 * Menggunakan OSRM real road routing, fallback ke Haversine jika OSRM gagal
 */
export async function getOptimalRoute(originLat, originLng, user) {
    // Rutekan ke SEMUA bin terdaftar (penuh atau tidak), skip koordinat 0,0
    const all = await findAllBins(user);
    const fullBins = (all || []).filter(b => b.lat != null && b.lng != null && !(b.lat === 0 && b.lng === 0));
    const routeTarget = 'all';

    if (!fullBins || fullBins.length === 0) {
        return { route: [], polyline: null, googleMapsUrl: null, message: 'Belum ada tempat sampah dengan koordinat terdaftar.' };
    }

    // Semua titik: [origin, ...bins]
    const allPoints = [
        { lat: originLat, lng: originLng },
        ...fullBins.map(b => ({ lat: b.lat, lng: b.lng })),
    ];

    let orderedBins;
    let polyline = null;
    let totalDistance = null;
    let totalDuration = null;
    let routingMethod = 'osrm';

    try {
        // 1. Duration matrix OSRM
        const matrix = await osrmTable(allPoints);

        // 2. TSP nearest neighbor pakai durasi jalan asli
        const order = tspNearestNeighbor(matrix);

        // order[0] = 0 (origin), order[1..] = index bin (1-based dalam allPoints)
        orderedBins = order.slice(1).map((idx, i) => {
            const bin = fullBins[idx - 1];
            return {
                ...bin,
                durationFromPreviousMin: Math.round(matrix[order[i]][idx] / 60),
            };
        });

        // 3. Polyline rute lengkap dari OSRM /route
        const routeCoords = order.map(i => allPoints[i]);
        const osrmRes = await osrmRoute(routeCoords);
        // OSRM returns [lon, lat] — flip ke [lat, lon] untuk Leaflet
        polyline = osrmRes.geometry.map(c => [c[1], c[0]]);
        totalDistance = osrmRes.distance;
        totalDuration = osrmRes.duration;

    } catch (err) {
        logger.warn(`[BinService] OSRM gagal, fallback Haversine: ${err.message}`);
        routingMethod = 'haversine-fallback';

        // Fallback: greedy nearest neighbor pakai Haversine
        let curLat = originLat, curLng = originLng;
        const unvisited = [...fullBins];
        orderedBins = [];
        while (unvisited.length > 0) {
            let nearestIdx = 0, minDist = Infinity;
            for (let i = 0; i < unvisited.length; i++) {
                const R = 6371;
                const dLat = (unvisited[i].lat - curLat) * Math.PI / 180;
                const dLon = (unvisited[i].lng - curLng) * Math.PI / 180;
                const a = Math.sin(dLat/2)**2 + Math.cos(curLat*Math.PI/180)*Math.cos(unvisited[i].lat*Math.PI/180)*Math.sin(dLon/2)**2;
                const d = R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
                if (d < minDist) { minDist = d; nearestIdx = i; }
            }
            const next = unvisited.splice(nearestIdx, 1)[0];
            next.durationFromPreviousMin = null;
            orderedBins.push(next);
            curLat = next.lat; curLng = next.lng;
        }
    }

    // Google Maps URL
    const dest = orderedBins[orderedBins.length - 1];
    let googleMapsUrl = `https://www.google.com/maps/dir/?api=1&origin=${originLat},${originLng}&destination=${dest.lat},${dest.lng}&travelmode=driving`;
    if (orderedBins.length > 1) {
        const waypoints = orderedBins.slice(0, -1).map(b => `${b.lat},${b.lng}`).join('|');
        googleMapsUrl += `&waypoints=${waypoints}`;
    }

    // QR Code
    const qrCodeBase64 = await QRCode.toDataURL(googleMapsUrl, {
        width: 400, margin: 2,
        color: { dark: '#000000', light: '#ffffff' },
    }).catch(() => null);

    return {
        route: orderedBins,
        polyline,
        totalDistanceKm: totalDistance ? +(totalDistance / 1000).toFixed(2) : null,
        totalDurationMin: totalDuration ? Math.round(totalDuration / 60) : null,
        routingMethod,
        routeTarget,
        googleMapsUrl,
        qrCodeBase64,
    };
}
