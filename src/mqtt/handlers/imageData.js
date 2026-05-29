import { prisma } from '../../config/db.js';
import { env } from '../../config/env.js';
import { broadcast } from '../../websocket/ws.js';
import { logger } from '../../utils/logger.js';

const VALID_LABELS = ['organik', 'anorganik', 'b3'];

/**
 * Forward bin image to Python classify service, persist result, broadcast WS event.
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

    let result;
    try {
        const res = await fetch(`${env.CLASSIFY_SERVICE_URL}/classify`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: imageB64 }),
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

    const label = VALID_LABELS.includes(result.label) ? result.label : 'unknown';
    const confidence = typeof result.confidence === 'number' ? result.confidence : 0;

    const record = await prisma.classification.create({
        data: {
            binId: bin.id,
            label,
            confidence,
            rawResult: result,
        },
    });

    broadcast('CLASSIFICATION_NEW', {
        id: record.id,
        nodeId,
        binId: bin.id,
        label,
        confidence,
        createdAt: record.createdAt,
    });

    logger.info(`[ImageHandler] ✓ ${nodeId} classified as ${label} (${confidence})`);
}
