import { z } from 'zod';
import { prisma } from '../../config/db.js';
import { redisClient } from '../../config/redis.js';
import { checkThreshold } from '../../services/alert.service.js';
import { attachWeightToLatestDeposit, createDeposit } from '../../models/deposit.model.js';
import { broadcast } from '../../websocket/ws.js';
import { distanceToFillPct, distanceToLabel } from '../../utils/fillLevel.js';
import { logger } from '../../utils/logger.js';
import {
    WEIGHT_MODE, getConfirmedWeight,
    getPendingLabel, clearPendingLabel, setConfirmedWeight,
} from '../../config/weightMode.js';

// Zod schema for sensor payload validation
// Sensor opsional: tong baru mungkin hanya punya GPS + laser (VL53L0X).
// Field tanpa sensor (load cell/volume/baterai) default 0 supaya kolom DB
// (NOT NULL) tetap terisi, tapi TIDAK dipakai untuk alert/berat (lihat di bawah).
const SensorPayloadSchema = z.object({
    weight:   z.number().min(0).max(200).optional(),   // kg — load cell (belum tentu ada)
    volume:   z.number().min(0).max(100).optional(),   // % — sensor volume (belum tentu ada)
    battery:  z.number().min(0).max(100).optional(),   // % — monitor baterai (belum tentu ada)
    gas:      z.number().min(0).optional(),            // ppm — MQ-x gas sensor
    distance: z.number().min(0).optional(),            // cm — VL53L0X laser
    lat:      z.number().optional(),                   // GPS latitude
    lng:      z.number().optional(),                   // GPS longitude
    rssi:     z.number().int().optional().default(-999),
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

    // 1b. Tingkat penuh dari laser VL53L0X.
    // Kalau tong belum punya sensor volume terpisah, jarak laser dipakai sebagai
    // sumber "volume %": jarak besar = kosong (0%), jarak kecil = penuh (100%).
    const fillFromLaser = distanceToFillPct(data.distance);
    if (data.volume == null && fillFromLaser != null) {
        data.volume = fillFromLaser; // dipakai untuk threshold & dashboard
    }

    // 2. Find bin by nodeId
    const bin = await prisma.bin.findUnique({ where: { nodeId } });
    if (!bin) {
        logger.warn(`[SensorHandler] Unknown nodeId: ${nodeId}. Data discarded.`);
        return;
    }

    // 3. Auto-update bin GPS coordinates if provided
    if (data.lat !== undefined && data.lng !== undefined) {
        await prisma.bin.update({
            where: { id: bin.id },
            data: { lat: data.lat, lng: data.lng },
        });
    }

    // 3b. Tentukan berat tong yang DIPAKAI untuk log & monitoring, sesuai mode.
    //  - sensor_pairing: berat mentah sensor TIDAK langsung dipakai; yang dipakai
    //    adalah berat tong TERKONFIRMASI terakhir (naik hanya setelah user konfirmasi).
    //  - loadcell / user: pakai berat dari payload apa adanya (perilaku lama).
    let effectiveWeight = data.weight ?? 0;
    if (WEIGHT_MODE === 'sensor_pairing') {
        effectiveWeight = await getConfirmedWeight(bin.id); // berat resmi terakhir
    }

    // 4. Save SensorLog to PostgreSQL
    // Kolom weight/volume/battery NOT NULL di DB → default 0 kalau sensornya belum ada.
    const log = await prisma.sensorLog.create({
        data: {
            binId:    bin.id,
            weight:   effectiveWeight,
            volume:   data.volume  ?? 0,
            battery:  data.battery ?? 0,
            gas:      data.gas ?? null,
            distance: data.distance ?? null,
            rssi:     data.rssi,
        },
    });

    // 5. Cache latest reading in Redis (TTL: 1 hour)
    if (redisClient) {
        try {
            const cacheKey = `bin:${nodeId}:latest`;
            await redisClient.set(
                cacheKey,
                // weight pakai effectiveWeight (berat resmi tong sesuai mode)
                JSON.stringify({ ...data, weight: effectiveWeight, timestamp: log.createdAt, logId: log.id }),
                'EX',
                3600
            );
        } catch (cacheErr) {
            logger.warn(`[SensorHandler] Failed to cache latest reading in Redis: ${cacheErr.message}`);
        }
    }

    // 6. Check thresholds and trigger alerts if needed
    await checkThreshold(nodeId, bin.id, data);

    // 6b. Penanganan BERAT sesuai WEIGHT_MODE (lihat config/weightMode.js)
    if (data.weight && data.weight > 0) {
        try {
            if (WEIGHT_MODE === 'sensor_pairing') {
                // URUTAN FISIK: jenis sudah dideteksi kamera DULU (pending label).
                // Sekarang BERAT tiba → pasangkan → buat deposit OTOMATIS.
                const pending = await getPendingLabel(bin.id);
                if (pending) {
                    await createDeposit({
                        userId:     pending.userId ?? null,
                        binId:      bin.id,
                        label:      pending.label,
                        confidence: pending.confidence ?? null,
                        weight:     data.weight,
                    });
                    await setConfirmedWeight(bin.id, data.weight); // jadi berat tong resmi
                    await clearPendingLabel(bin.id);
                    effectiveWeight = data.weight;                 // langsung kebaca di BIN_UPDATE
                    await broadcast('DEPOSIT_AUTO', {
                        nodeId, binId: bin.id, label: pending.label, weight: data.weight,
                    });
                    logger.info(`[SensorHandler] deposit OTOMATIS: ${pending.label} ${data.weight}kg → ${nodeId}`);
                } else {
                    // Berat datang tanpa jenis pending (kamera belum klasifikasi / window lewat).
                    logger.debug(`[SensorHandler] berat ${data.weight}kg tiba tapi tak ada jenis pending ${nodeId}`);
                }
            } else if (WEIGHT_MODE === 'loadcell') {
                // [AKTIF saat load cell terpasang] tempel berat otomatis ke deposit terakhir.
                const updated = await attachWeightToLatestDeposit(bin.id, data.weight);
                if (updated) logger.debug(`[SensorHandler] berat ${data.weight}kg → deposit ${updated.id}`);
            }
            // WEIGHT_MODE === 'user': berat dari input /ecosort, sensor diabaikan. [HAPUS saat full-sensor]
        } catch (e) {
            logger.warn(`[SensorHandler] gagal proses berat: ${e.message}`);
        }
    }

    // 7. Broadcast to WebSocket clients
    await broadcast('BIN_UPDATE', {
        nodeId,
        binId:    bin.id,
        weight:   effectiveWeight,      // berat resmi tong (sesuai mode)
        volume:   data.volume,          // sudah termasuk fill% hasil laser kalau sensor volume belum ada
        fillPct:  data.volume ?? null,  // tingkat penuh (%) untuk dashboard
        fillLabel: distanceToLabel(data.distance), // KOSONG/SEDANG/HAMPIR PENUH/PENUH
        battery:  data.battery,
        gas:      data.gas ?? null,
        distance: data.distance ?? null,
        rssi:     data.rssi,
        timestamp: log.createdAt,
    });

    logger.debug(`[SensorHandler] ✓ Saved log for ${nodeId} | w=${data.weight}kg v=${data.volume}% b=${data.battery}% g=${data.gas ?? '-'}ppm`);
}

