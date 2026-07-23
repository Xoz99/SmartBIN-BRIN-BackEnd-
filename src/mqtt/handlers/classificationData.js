import { prisma } from '../../config/db.js';
import { mqttClient } from '../../config/mqtt.js';
import { broadcast } from '../../websocket/ws.js';
import { logger } from '../../utils/logger.js';
import { WEIGHT_MODE, setPendingLabel } from '../../config/weightMode.js';

const VALID_LABELS = ['organik', 'anorganik', 'b3'];

/**
 * Terima hasil pemilahan yang SUDAH diklasifikasi oleh device (raspi-pemilah).
 * Berbeda dari imageData.js: tidak memanggil classify service — label sudah jadi.
 * Topik: smartbin/{nodeId}/classification  payload: { label, confidence }
 * @param {string} nodeId
 * @param {object|string} payload
 * @returns {Promise<object|null>} record klasifikasi, atau null jika nodeId tak dikenal
 */
export async function handleClassificationData(nodeId, payload) {
    const bin = await prisma.bin.findUnique({ where: { nodeId } });
    if (!bin) {
        logger.warn(`[ClassificationHandler] Unknown nodeId: ${nodeId}. Discarded.`);
        return null;
    }

    const rawLabel = typeof payload === 'object' && payload ? payload.label : payload;
    const label = VALID_LABELS.includes(rawLabel) ? rawLabel : 'unknown';
    const confidence =
        typeof payload === 'object' && typeof payload.confidence === 'number'
            ? payload.confidence
            : 0;

    // Simpan ke DB
    const record = await prisma.classification.create({
        data: {
            binId: bin.id,
            label,
            confidence,
            rawResult: typeof payload === 'object' && payload ? payload : { label },
        },
    });

    // sensor_pairing: tahan jenis sebagai "pending label" → deposit terbentuk
    // otomatis saat berat load cell tiba (handlers/sensorData.js).
    if (label !== 'unknown' && WEIGHT_MODE === 'sensor_pairing') {
        await setPendingLabel(bin.id, { label, confidence, userId: null });
        await broadcast('LABEL_PENDING', { nodeId, binId: bin.id, label, confidence });
    }

    // Perintah aktuasi balik ke device (gerakkan pemilah)
    if (label !== 'unknown' && mqttClient?.connected) {
        const commandTopic = `smartbin/${nodeId}/command`;
        mqttClient.publish(commandTopic, JSON.stringify({ action: label }), { qos: 1 });
        logger.info(`[ClassificationHandler] command → ${commandTopic}: ${label}`);
    }

    // Broadcast ke frontend
    await broadcast('CLASSIFICATION_NEW', {
        id: record.id,
        nodeId,
        binId: bin.id,
        label,
        confidence,
        createdAt: record.createdAt,
    });

    logger.info(`[ClassificationHandler] ✓ ${nodeId} → ${label} (${(confidence * 100).toFixed(1)}%)`);

    return record;
}
