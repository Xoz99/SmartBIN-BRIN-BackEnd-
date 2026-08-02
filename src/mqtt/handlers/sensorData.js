import { z } from 'zod';
import { prisma } from '../../config/db.js';
import { redisClient } from '../../config/redis.js';
import { checkThreshold } from '../../services/alert.service.js';
import { attachWeightToLatestDeposit, createDeposit } from '../../models/deposit.model.js';
import { broadcast } from '../../websocket/ws.js';
import { distanceToFillPct, distanceToLabel, fillPctToLabel } from '../../utils/fillLevel.js';
import { normalizeSensorPayload } from '../adapters/hardwareSensor.js';
import { logger } from '../../utils/logger.js';
import {
    WEIGHT_MODE, getConfirmedWeight,
    getPendingLabel, clearPendingLabel, setConfirmedWeight,
    recordWeighing,
} from '../../config/weightMode.js';

/**
 * Berapa lama tong dianggap masih "online" setelah pesan sensor terakhir.
 * Dokumentasi hardware: tidak ada pesan > 30 detik → tandai offline
 * (EcoSort publish tiap 5 detik, jadi 30 detik = 6× interval).
 */
const SENSOR_ONLINE_TTL_SEC = Number(process.env.BIN_OFFLINE_GAP_SEC ?? 30);

// Zod schema for sensor payload validation
// Sensor opsional: tong baru mungkin hanya punya GPS + laser (VL53L0X).
// Field tanpa sensor (load cell/volume/baterai) default 0 supaya kolom DB
// (NOT NULL) tetap terisi, tapi TIDAK dipakai untuk alert/berat (lihat di bawah).
const SensorPayloadSchema = z.object({
    weight:   z.number().min(0).max(200).optional(),   // kg — load cell (belum tentu ada)
    volume:   z.number().min(0).max(100).optional(),   // % — sensor volume (belum tentu ada)
    battery:  z.number().min(0).max(100).optional(),   // % — monitor baterai (belum tentu ada)
    batteryVoltage: z.number().min(0).optional(),      // V — tegangan baterai (INA219, EcoSort)
    gas:      z.number().min(0).optional(),            // ppm — MQ-x gas sensor
    distance: z.number().min(0).optional(),            // cm — VL53L0X laser
    lat:      z.number().optional(),                   // GPS latitude
    lng:      z.number().optional(),                   // GPS longitude
    rssi:     z.number().int().optional().default(-999),
    snr:      z.number().optional(),                   // dB — LoRa SNR (dari board RX [RF IN])
    packetLen: z.number().int().min(0).optional(),     // bytes — panjang paket RF (metrik penelitian)
    // ── Perbandingan transport LoRa vs HTTP/internet ──
    transport: z.enum(['lora', 'http']).optional(),    // jalur data sampai ke backend
    seq:      z.number().int().min(0).optional(),      // nomor urut paket dari device (packet loss)
    sentAt:   z.string().optional(),                   // ISO — jam device saat kirim (latency)
    throughputBps: z.number().min(0).optional(),       // bit/detik — throughput jalur (diukur di gateway)
});

/**
 * Handle incoming sensor data from a bin node
 * @param {string} nodeId
 * @param {object} rawPayload
 */
/**
 * Nilai gas SIMULASI (ppm) — dipakai selagi perangkat belum punya sensor gas,
 * supaya kartu Gas di dashboard tidak kosong. Random-walk halus per node biar
 * terlihat "hidup" & realistis, ditahan di rentang SEHAT (<100 ppm).
 *   < 100 ppm = sehat  |  > 100 ppm = tidak sehat
 * Env:
 *   GAS_SIMULATE=0        → matikan simulasi (kartu jadi "—" kalau sensor belum ada)
 *   GAS_SIMULATE_MIN/MAX  → ubah rentang (mis. MAX=150 untuk uji kondisi tidak sehat)
 */
const _gasState = new Map(); // nodeId → nilai gas terakhir (untuk random-walk halus)
function simulateGas(nodeId) {
    const MIN = Number(process.env.GAS_SIMULATE_MIN ?? 35);
    const MAX = Number(process.env.GAS_SIMULATE_MAX ?? 85);
    const prev = _gasState.get(nodeId) ?? (MIN + MAX) / 2;
    let next = prev + (Math.random() * 12 - 6); // langkah acak ±6 ppm
    next = Math.max(MIN, Math.min(MAX, next));   // clamp ke rentang
    _gasState.set(nodeId, next);
    return Math.round(next);
}

export async function handleSensorData(nodeId, rawPayload) {
    // 0. Payload perangkat EcoSort bertingkat (3 kompartemen + battery object) →
    //    flatten dulu. Payload flat versi lama lewat tanpa diubah.
    const { payload, compartments } = normalizeSensorPayload(rawPayload);

    // 1. Validate payload
    const parsed = SensorPayloadSchema.safeParse(payload);
    if (!parsed.success) {
        logger.warn(`[SensorHandler] Invalid payload from ${nodeId}:`, parsed.error.flatten());
        return;
    }
    const data = parsed.data;

    // 1a. GAS: perangkat belum punya sensor gas → isi nilai SIMULASI yang wajar
    // (<100 ppm = sehat). Matikan dengan env GAS_SIMULATE=0.
    if (data.gas == null && process.env.GAS_SIMULATE !== '0') {
        data.gas = simulateGas(nodeId);
    }

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
    //  - accumulate: berat tong = TOTAL berjalan (jumlah semua penimbangan).
    //  - loadcell / user: pakai berat dari payload apa adanya (perilaku lama).
    let effectiveWeight = data.weight ?? 0;
    if (WEIGHT_MODE === 'sensor_pairing' || WEIGHT_MODE === 'accumulate') {
        effectiveWeight = await getConfirmedWeight(bin.id); // berat resmi / total terakhir
    }

    // 3c. Mode AKUMULASI: deteksi satu penimbangan selesai (berat naik lalu balik
    // ~0) lalu jumlahkan berat PUNCAK episode ke total. Dijalankan SEBELUM log &
    // cache supaya SensorLog + broadcast langsung memuat total terbaru.
    if (WEIGHT_MODE === 'accumulate') {
        const w = await recordWeighing(bin.id, data.weight);
        if (w.committed) {
            effectiveWeight = w.total;
            // Kalau kamera sempat klasifikasi jenis pada episode ini → buat deposit.
            const pending = await getPendingLabel(bin.id);
            if (pending) {
                await createDeposit({
                    userId:     pending.userId ?? null,
                    binId:      bin.id,
                    label:      pending.label,
                    confidence: pending.confidence ?? null,
                    weight:     w.added,
                });
                await clearPendingLabel(bin.id);
            }
            await broadcast('DEPOSIT_AUTO', {
                nodeId, binId: bin.id,
                label:  pending?.label ?? null,
                weight: w.added, total: w.total,
            });
            logger.info(`[SensorHandler] +${w.added}kg → total ${w.total}kg (${nodeId})`);
        }
    }

    // 3b. Latency device→backend: createdAt(terima) − sentAt(kirim). Offset jam
    // device saling meniadakan saat LoRa vs HTTP dibandingkan (device sama).
    let sentAtDate = null;
    let latencyMs = null;
    if (data.sentAt) {
        const t = new Date(data.sentAt).getTime();
        if (!Number.isNaN(t)) {
            sentAtDate = new Date(t);
            latencyMs = Math.max(0, Date.now() - t);
        }
    }

    // Throughput jalur HTTP tidak diukur di device (LoRa dihitung dari airtime RF
    // di gateway). Turunkan di sini dari ukuran paket & latency end-to-end
    // (bit / detik) → sebanding untuk perbandingan LoRa vs HTTP.
    let throughputBps = data.throughputBps ?? null;
    if (throughputBps == null && data.transport === 'http' && data.packetLen && latencyMs > 0) {
        throughputBps = (data.packetLen * 8) / (latencyMs / 1000);
    }

    // 4. Save SensorLog to PostgreSQL
    // Kolom weight/volume/battery NOT NULL di DB → default 0 kalau sensornya belum ada.
    const log = await prisma.sensorLog.create({
        data: {
            binId:    bin.id,
            weight:   effectiveWeight,
            volume:   data.volume  ?? 0,
            battery:  data.battery ?? 0,
            batteryVoltage: data.batteryVoltage ?? null,
            gas:      data.gas ?? null,
            distance: data.distance ?? null,
            rssi:     data.rssi,
            snr:      data.snr ?? null,
            packetLen: data.packetLen ?? null,
            transport: data.transport ?? null,
            seq:      data.seq ?? null,
            sentAt:   sentAtDate,
            latencyMs,
            throughputBps,
        },
    });

    // 5. Cache latest reading in Redis (TTL: 1 hour)
    if (redisClient) {
        try {
            const cacheKey = `bin:${nodeId}:latest`;
            await redisClient.set(
                cacheKey,
                // weight pakai effectiveWeight (berat resmi tong sesuai mode)
                JSON.stringify({
                    ...data,
                    weight: effectiveWeight,          // berat terkonfirmasi tong (sesuai WEIGHT_MODE)
                    weightRaw: data.weight ?? 0,      // berat scale LIVE (berat_g→kg) untuk monitoring
                    ...(compartments ? { compartments } : {}),
                    timestamp: log.createdAt,
                    logId: log.id,
                }),
                'EX',
                3600
            );
        } catch (cacheErr) {
            logger.warn(`[SensorHandler] Failed to cache latest reading in Redis: ${cacheErr.message}`);
        }

        // 5b. Data sensor = bukti tong hidup. Tong EcoSort TIDAK publish topic
        // `status` (bridge Raspi hanya publish sensor), jadi kalau status cuma
        // diperbarui dari heartbeat, tong akan selalu tampil offline.
        // Aturan dokumentasi hardware: tidak ada pesan > 30 detik → offline.
        try {
            const statusKey = `bin:${nodeId}:status`;
            // Jangan PERPENDEK TTL yang sudah dipasang heartbeat (tong ESP32 lama
            // pakai TTL 3 menit dan interval sensornya bisa > 30 detik).
            const currentTtl = await redisClient.ttl(statusKey);
            const ttl = Math.max(SENSOR_ONLINE_TTL_SEC, currentTtl > 0 ? currentTtl : 0);

            await redisClient.set(statusKey, 'online', 'EX', ttl);
            await redisClient.set(`bin:${nodeId}:lastSeen`, new Date().toISOString(), 'EX', 86400);
        } catch (statusErr) {
            logger.warn(`[SensorHandler] Gagal perbarui status online: ${statusErr.message}`);
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
        weightRaw: data.weight ?? 0,    // berat scale LIVE (berat_g→kg) untuk monitoring
        volume:   data.volume,          // sudah termasuk fill% hasil laser kalau sensor volume belum ada
        fillPct:  data.volume ?? null,  // tingkat penuh (%) untuk dashboard
        // Tong EcoSort sudah hitung volume di firmware → label dari volume, bukan
        // dari jarak (ambang jarak di sini beda dengan firmware EcoSort).
        fillLabel: compartments
            ? fillPctToLabel(data.volume)
            : distanceToLabel(data.distance), // KOSONG/SEDANG/HAMPIR PENUH/PENUH
        // rincian per kompartemen (organik/anorganik/b3) — null untuk node lama
        compartments: compartments ?? null,
        battery:  data.battery,
        batteryVoltage: data.batteryVoltage ?? null,
        gas:      data.gas ?? null,
        distance: data.distance ?? null,
        rssi:     data.rssi,
        snr:      data.snr ?? null,
        packetLen: data.packetLen ?? null,
        transport: data.transport ?? null,
        seq:      data.seq ?? null,
        latencyMs,
        throughputBps,
        timestamp: log.createdAt,
    });

    logger.debug(`[SensorHandler] ✓ Saved log for ${nodeId} | w=${data.weight}kg v=${data.volume}% b=${data.battery}% g=${data.gas ?? '-'}ppm`);
}

