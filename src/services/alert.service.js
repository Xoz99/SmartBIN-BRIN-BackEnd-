import { findActiveAlert, createAlert, escalateAlert, findAllAlerts, resolveAlert as resolveAlertModel } from '../models/alert.model.js';
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

    // Baterai: kalau tong mengirim TEGANGAN (EcoSort/INA219), ambang tegangan yang
    // dipakai (dokumentasi hardware §6). Tong lama yang cuma kirim persen tetap
    // pakai ambang persen seperti sebelumnya.
    const volts = sensorData.batteryVoltage;
    const pakaiTegangan = volts != null;

    const bateraiKritis = pakaiTegangan && volts < env.BATTERY_VOLTAGE_CRITICAL;
    const bateraiLemah = pakaiTegangan
        ? volts < env.BATTERY_VOLTAGE_WARNING
        : sensorData.battery != null && sensorData.battery <= threshold.battery;

    const volumeKritis =
        sensorData.volume != null && sensorData.volume >= env.VOLUME_CRITICAL_THRESHOLD;

    // Baterai pulih: pakai ambang PEMULIHAN yang sedikit di atas ambang alert
    // (histeresis), supaya tegangan yang naik-turun sedikit di sekitar ambang
    // tidak bikin alert nyala-mati terus.
    const bateraiPulih = pakaiTegangan
        ? volts >= env.BATTERY_VOLTAGE_RECOVERY
        : sensorData.battery != null && sensorData.battery > threshold.battery + 5;

    // `resolveWhen` HARUS ditulis eksplisit, tidak boleh sekadar kebalikan dari
    // `condition`: condition juga false kalau sensornya TIDAK MENGIRIM data
    // (mis. EcoSort tanpa load cell → weight undefined). Kalau auto-resolve ikut
    // jalan di situ, alert tong penuh bisa hilang sendiri dan pickup petugas
    // salah tertandai "terverifikasi sensor" padahal tong belum dikosongkan.
    const checks = [
        {
            // hanya dicek kalau sensornya benar-benar mengirim (tong tanpa load cell → skip)
            condition: sensorData.weight != null && sensorData.weight >= threshold.weight,
            resolveWhen: sensorData.weight != null && sensorData.weight < threshold.weight,
            type: 'FULL_WEIGHT',
            severity: 'WARNING',
            message: `Tong ${nodeId}: Berat ${sensorData.weight} kg melebihi ambang ${threshold.weight} kg`,
        },
        {
            condition: sensorData.volume != null && sensorData.volume >= threshold.volume,
            resolveWhen: sensorData.volume != null && sensorData.volume < threshold.volume,
            type: 'FULL_VOLUME',
            severity: volumeKritis ? 'CRITICAL' : 'WARNING',
            message: volumeKritis
                ? `Tong ${nodeId}: PENUH (${sensorData.volume}%) — perlu dikosongkan`
                : `Tong ${nodeId}: Hampir penuh, volume ${sensorData.volume}% melebihi ambang ${threshold.volume}%`,
        },
        {
            // baterai: skip kalau tong belum punya sensor baterai (jangan alert palsu)
            condition: bateraiLemah,
            // INA219 lepas (battery.ok=false) → tidak ada bacaan → JANGAN resolve:
            // baterai lemah tidak jadi sembuh hanya karena sensornya copot.
            resolveWhen: bateraiPulih,
            type: 'BATTERY_LOW',
            severity: bateraiKritis ? 'CRITICAL' : 'WARNING',
            message: pakaiTegangan
                ? (bateraiKritis
                    ? `Tong ${nodeId}: Baterai KRITIS ${volts}V (ambang ${env.BATTERY_VOLTAGE_CRITICAL}V) — segera isi daya`
                    : `Tong ${nodeId}: Baterai hampir habis ${volts}V (ambang ${env.BATTERY_VOLTAGE_WARNING}V)`)
                : `Tong ${nodeId}: Baterai lemah ${sensorData.battery}% (ambang ${threshold.battery}%)`,
        },
        {
            condition: sensorData.gas != null && sensorData.gas >= threshold.gas,
            resolveWhen: sensorData.gas != null && sensorData.gas < threshold.gas,
            type: 'GAS_HIGH',
            severity: 'WARNING',
            message: `Tong ${nodeId}: Kadar gas tinggi ${sensorData.gas} ppm (ambang ${threshold.gas} ppm)`,
        },
    ];

    for (const check of checks) {
        if (!check.condition) {
            // Auto-resolve (Checkpoint Otomatis)
            // Sensor membaca kondisi sudah kembali normal (di bawah ambang) →
            // alert aktif yang lama di-resolve otomatis.
            if (check.resolveWhen) {
                const existing = await findActiveAlert(binId, check.type);
                if (existing) {
                    await resolveAlertModel(existing.id);
                    logger.info(`[AlertService] 🧹 Auto-resolved ${check.type} alert for ${nodeId}`);

                    await broadcast('ALERT_RESOLVED', {
                        alertId: existing.id,
                        nodeId,
                        binId,
                        type: check.type
                    });

                    // Checkpoint sensor: kalau ada petugas yg sudah tekan "Selesai"
                    // (pickup MENUNGGU_SENSOR), tandai pickup tsb terverifikasi SELESAI.
                    // Hanya berat/volume yang membuktikan tong sudah dikosongkan —
                    // baterai pulih atau gas turun jelas bukan bukti pengosongan.
                    if (check.type === 'FULL_WEIGHT' || check.type === 'FULL_VOLUME') {
                        await confirmPickupBySensor(binId, nodeId).catch((err) =>
                            logger.error('[AlertService] Gagal konfirmasi pickup via sensor:', err.message)
                        );
                    }
                }
            }
            continue;
        }

        // Deduplicate — skip if alert already active
        const existing = await findActiveAlert(binId, check.type);
        if (existing) {
            // ...kecuali kalau kondisinya MEMBURUK (WARNING → CRITICAL): naikkan
            // tingkatnya supaya petugas tetap dapat notifikasi darurat.
            if (check.severity === 'CRITICAL' && existing.severity !== 'CRITICAL') {
                const naik = await escalateAlert(existing.id, {
                    severity: check.severity,
                    message: check.message,
                });

                logger.warn(`[AlertService] 🚨 Alert naik ke CRITICAL: ${check.message}`);

                await broadcast('ALERT_ESCALATED', {
                    alertId: naik.id,
                    nodeId,
                    binId,
                    type: check.type,
                    severity: check.severity,
                    message: check.message,
                    areaId: bin.areaId,
                });

                await sendPushNotif({ ...naik, bin: { areaId: bin.areaId } }).catch((err) =>
                    logger.error('[AlertService] FCM push failed:', err.message)
                );
                continue;
            }

            logger.debug(`[AlertService] Active alert already exists for ${nodeId} / ${check.type}`);
            continue;
        }

        // Create alert record
        const alert = await createAlert({
            binId,
            type: check.type,
            severity: check.severity,
            message: check.message,
        });

        logger.warn(`[AlertService] 🚨 Alert created: ${check.message}`);

        // Broadcast via WebSocket
        await broadcast('ALERT_NEW', {
            alertId: alert.id,
            nodeId,
            binId,
            type: check.type,
            severity: check.severity,
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

