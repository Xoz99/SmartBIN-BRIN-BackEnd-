import { createDeposit, findDeposits } from '../models/deposit.model.js';
import { findBinByNodeId } from '../models/bin.model.js';
import { createMqttClient } from '../config/mqtt.js';
import { commandTopic } from '../mqtt/topics.js';
import { logger } from '../utils/logger.js';
import {
    WEIGHT_MODE, setConfirmedWeight, setPendingLabel,
} from '../config/weightMode.js';

/**
 * Catat setoran sampah + kirim perintah pilah ke ESP bin tujuan via MQTT.
 * URUTAN FISIK: jenis dideteksi DULU, berat menyusul.
 * Berat ditentukan oleh WEIGHT_MODE (lihat config/weightMode.js):
 *  - sensor_pairing: endpoint ini = KLASIFIKASI MANUAL (jenis dulu). Simpan jenis
 *                    sebagai "pending label" → deposit dibuat OTOMATIS saat berat
 *                    load cell tiba via MQTT (lihat handlers/sensorData.js).
 *                    TIDAK membuat deposit di sini. Kalau weight dikirim eksplisit
 *                    (mis. demo tanpa hardware), langsung commit deposit pakai weight itu.
 *  - user:           pakai weight dari body /ecosort (input manual). [HAPUS saat full-sensor]
 *  - loadcell:       weight null; nanti diisi sensor via attachWeightToLatestDeposit.
 * @param {{userId:string, nodeId:string, label:string, confidence?:number, weight?:number}} input
 */
export async function recordDeposit({ userId, nodeId, label, confidence, weight }) {
    const bin = await findBinByNodeId(nodeId);
    if (!bin) throw Object.assign(new Error('Bin tidak ditemukan'), { statusCode: 404 });

    // sensor_pairing + TANPA weight eksplisit → tahan label, deposit otomatis pas berat tiba.
    if (WEIGHT_MODE === 'sensor_pairing' && (weight == null || weight <= 0)) {
        await setPendingLabel(bin.id, { label, confidence, userId });
        try {
            const client = createMqttClient();
            client.publish(commandTopic(nodeId), JSON.stringify({ action: 'sort', label }));
        } catch (err) {
            logger.warn(`[Deposit] gagal publish MQTT: ${err.message}`);
        }
        logger.info(`[Deposit] jenis '${label}' PENDING (nunggu berat) → ${nodeId}`);
        return { pending: true, label, nodeId, binId: bin.id };
    }

    // Tentukan berat deposit sesuai mode
    let finalWeight;
    if (WEIGHT_MODE === 'sensor_pairing') {
        // weight dikirim eksplisit (demo/manual) → commit langsung
        finalWeight = weight;
        await setConfirmedWeight(bin.id, weight);
        logger.info(`[Deposit] berat manual ${weight}kg → ${nodeId}`);
    } else if (WEIGHT_MODE === 'user') {
        finalWeight = weight ?? null;              // [HAPUS saat full-sensor]
    } else { // 'loadcell'
        finalWeight = null;                        // diisi sensor belakangan
    }

    const deposit = await createDeposit({ userId, binId: bin.id, label, confidence: confidence ?? null, weight: finalWeight });

    // Kirim perintah pilah ke ESP (best-effort, tidak menggagalkan pencatatan)
    try {
        const client = createMqttClient();
        client.publish(commandTopic(nodeId), JSON.stringify({ action: 'sort', label }));
        logger.info(`[Deposit] command → ${commandTopic(nodeId)} {label:${label}}`);
    } catch (err) {
        logger.warn(`[Deposit] gagal publish MQTT: ${err.message}`);
    }

    return { ...deposit, nodeId };
}

export async function listDeposits(filter) {
    return findDeposits(filter);
}
