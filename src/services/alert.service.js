import { findActiveAlert, createAlert, findAllAlerts, resolveAlert as resolveAlertModel } from '../models/alert.model.js';
import { getBinThreshold } from './bin.service.js';
import { sendPushNotif } from './notify.service.js';
import { confirmPickupBySensor } from './pickup.service.js';
import { broadcast } from '../websocket/ws.js';
import { env } from '../config/env.js';
import { logger } from '../utils/logger.js';

import { findBinById } from '../models/bin.model.js';

/**
 * Compare sensor data against thresholds and generate alerts if breached.
 * Deduplicates — won't create duplicate unresolved alerts.
 *
 * @param {string} nodeId
 * @param {string} binId
 * @param {{ weight: number, volume: number, battery: number }} sensorData
 */
export async function checkThreshold(nodeId, binId, sensorData) {
    const bin = await findBinById(binId);
    if (!bin) return;

    const threshold = await getBinThreshold(nodeId);

    const checks = [
        {
            condition: sensorData.weight >= threshold.weight,
            type: 'FULL_WEIGHT',
            message: `Bin ${nodeId}: Weight ${sensorData.weight}kg has reached threshold ${threshold.weight}kg`,
        },
        {
            condition: sensorData.volume >= threshold.volume,
            type: 'FULL_VOLUME',
            message: `Bin ${nodeId}: Volume ${sensorData.volume}% has reached threshold ${threshold.volume}%`,
        },
        {
            condition: sensorData.battery <= threshold.battery,
            type: 'BATTERY_LOW',
            message: `Bin ${nodeId}: Battery low at ${sensorData.battery}% (threshold ${threshold.battery}%)`,
        },
        {
            condition: sensorData.gas != null && sensorData.gas >= threshold.gas,
            type: 'GAS_HIGH',
            message: `Bin ${nodeId}: Gas level high at ${sensorData.gas}ppm (threshold ${threshold.gas}ppm)`,
        },
    ];

    for (const check of checks) {
        if (!check.condition) {
            // Auto-resolve (Checkpoint Otomatis)
            // Jika sensor membaca volume/berat sudah turun (di bawah threshold) 
            // dan sebelumnya ada alert aktif, maka sistem otomatis me-resolve alert tersebut
            if (check.type === 'FULL_WEIGHT' || check.type === 'FULL_VOLUME') {
                const existing = await findActiveAlert(binId, check.type);
                if (existing) {
                    await resolveAlertModel(existing.id);
                    logger.info(`[AlertService] 🧹 Auto-resolved ${check.type} alert for ${nodeId} (Bin has been emptied)`);
                    
                    broadcast('ALERT_RESOLVED', {
                        alertId: existing.id,
                        nodeId,
                        binId,
                        type: check.type
                    });

                    // Checkpoint sensor: kalau ada petugas yg sudah tekan "Selesai"
                    // (pickup MENUNGGU_SENSOR), tandai pickup tsb terverifikasi SELESAI.
                    await confirmPickupBySensor(binId, nodeId).catch((err) =>
                        logger.error('[AlertService] Gagal konfirmasi pickup via sensor:', err.message)
                    );
                }
            }
            continue;
        }

        // Deduplicate — skip if alert already active
        const existing = await findActiveAlert(binId, check.type);
        if (existing) {
            logger.debug(`[AlertService] Active alert already exists for ${nodeId} / ${check.type}`);
            continue;
        }

        // Create alert record
        const alert = await createAlert({
            binId,
            type: check.type,
            message: check.message,
        });

        logger.warn(`[AlertService] 🚨 Alert created: ${check.message}`);

        // Broadcast via WebSocket
        broadcast('ALERT_NEW', {
            alertId: alert.id,
            nodeId,
            binId,
            type: check.type,
            message: check.message,
            createdAt: alert.createdAt,
            areaId: bin.areaId, // Include for frontend filtering
        });

        // Push notification
        await sendPushNotif({ ...alert, bin: { areaId: bin.areaId } }).catch((err) =>
            logger.error('[AlertService] FCM push failed:', err.message)
        );
    }
}

/**
 * Get alerts list with optional filter and area scoping
 * @param {object} user - The user requesting the alerts
 * @param {{ resolved?: boolean }} filters
 * @param {number} limit
 * @param {number} page
 */
export async function getAlerts(user, { resolved } = {}, limit = 50, page = 1) {
    return findAllAlerts(user, { resolved }, limit, page);
}

/**
 * Mark an alert as resolved
 * @param {string} alertId
 */
export async function resolveAlert(alertId) {
    return resolveAlertModel(alertId);
}

/**
 * Get an alert with its bin info (for area ownership check)
 * @param {string} alertId
 */
export async function getAlertWithBin(alertId) {
    const { prisma } = await import('../config/db.js');
    return prisma.alert.findUnique({
        where: { id: alertId },
        include: { bin: { select: { areaId: true } } },
    });
}

