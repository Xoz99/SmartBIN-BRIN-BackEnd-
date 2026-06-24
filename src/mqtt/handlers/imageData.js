import { prisma } from '../../config/db.js';
import { mqttClient } from '../../config/mqtt.js';
import { env } from '../../config/env.js';
import { broadcast } from '../../websocket/ws.js';
import { logger } from '../../utils/logger.js';
import { WEIGHT_MODE, setPendingLabel } from '../../config/weightMode.js';

const VALID_LABELS = ['organik', 'anorganik', 'b3'];

/**
 * Forward bin image to EcoSort classify service, persist result,
 * broadcast WS event, and publish actuation command back to ESP32.
 * @param {string} nodeId
 * @param {string|object} payload — either raw base64 string or { image: <base64> }
 */
export async function handleImageData(nodeId, payload) {
    const bin = await prisma.bin.findUnique({ where: { nodeId } });
    if (!bin) {
        logger.warn(`[ImageHandler] Unknown nodeId: ${nodeId}. Image discarded.`);
        return;
    }

    const imageB64 = typeof payload === 'string' ? payload : payload?.image;
    if (!imageB64 || typeof imageB64 !== 'string') {
        logger.warn(`[ImageHandler] No base64 image in payload from ${nodeId}`);
        return;
    }

    // Convert base64 → Buffer → Blob for multipart form upload
    let result;
    try {
        const imageBuffer = Buffer.from(imageB64, 'base64');
        const blob = new Blob([imageBuffer], { type: 'image/jpeg' });
        const formData = new FormData();
        formData.append('file', blob, 'capture.jpg');

        const res = await fetch(`${env.CLASSIFY_SERVICE_URL}/predict/`, {
            method: 'POST',
            body: formData,
        });

        if (!res.ok) {
            logger.error(`[ImageHandler] classify service ${res.status}: ${await res.text()}`);
            return;
        }
        result = await res.json();
    } catch (err) {
        logger.error(`[ImageHandler] classify service unreachable: ${err.message}`);
        return;
    }

    // EcoSort returns { status: "success", hasil: [{ kategori, confidence }] }
    if (result.status !== 'success' || !result.hasil?.length) {
        logger.warn(`[ImageHandler] classify returned no result for ${nodeId}`);
        return;
    }

    const label = VALID_LABELS.includes(result.hasil[0].kategori)
        ? result.hasil[0].kategori
        : 'unknown';
    const confidence = typeof result.hasil[0].confidence === 'number'
        ? result.hasil[0].confidence
        : 0;

    // Persist to DB
    const record = await prisma.classification.create({
        data: {
            binId:     bin.id,
            label,
            confidence,
            rawResult: result,
        },
    });

    // sensor_pairing: JENIS terdeteksi DULU → tahan sebagai "pending label"
    // (window PAIRING_TTL_SEC). Deposit dibuat OTOMATIS saat berat load cell tiba
    // (lihat handlers/sensorData.js). userId null = setoran sistem (bukan warga).
    if (label !== 'unknown' && WEIGHT_MODE === 'sensor_pairing') {
        await setPendingLabel(bin.id, { label, confidence, userId: null });
        await broadcast('LABEL_PENDING', { nodeId, binId: bin.id, label, confidence });
        logger.info(`[ImageHandler] jenis '${label}' PENDING (nunggu berat) ${nodeId}`);
    }

    // Publish actuation command back to ESP32
    if (label !== 'unknown' && mqttClient?.connected) {
        const commandTopic = `smartbin/${nodeId}/command`;
        mqttClient.publish(commandTopic, JSON.stringify({ action: label }), { qos: 1 });
        logger.info(`[ImageHandler] Published command to ${commandTopic}: ${label}`);
    }

    // Broadcast to frontend via WebSocket
    broadcast('CLASSIFICATION_NEW', {
        id:         record.id,
        nodeId,
        binId:      bin.id,
        label,
        confidence,
        createdAt:  record.createdAt,
    });

    logger.info(`[ImageHandler] ✓ ${nodeId} classified as ${label} (${(confidence * 100).toFixed(1)}%)`);
}
