import fetch from 'node-fetch';
import FormData from 'form-data';
import { env } from '../config/env.js';
import { logger } from '../utils/logger.js';

/**
 * Classify an image by forwarding it to the Python YOLOv5 microservice
 *
 * @param {Buffer|string} imageData — Buffer (file upload) or base64 string
 * @param {'buffer'|'base64'} mode
 * @returns {{ label: string, confidence: number, all_detections: object[] }}
 */
export async function classifyImage(imageData, mode = 'buffer') {
    const url = `${env.CLASSIFY_SERVICE_URL}/classify`;

    let response;

    if (mode === 'buffer') {
        const form = new FormData();
        form.append('file', imageData, { filename: 'image.jpg', contentType: 'image/jpeg' });

        response = await fetch(url, {
            method: 'POST',
            body: form,
            headers: form.getHeaders(),
            timeout: 15_000,
        });
    } else {
        // base64 mode — send JSON
        response = await fetch(url, {
            method: 'POST',
            body: JSON.stringify({ image: imageData }),
            headers: { 'Content-Type': 'application/json' },
            timeout: 15_000,
        });
    }

    if (!response.ok) {
        const errText = await response.text();
        logger.error(`[ClassifyService] Microservice error ${response.status}: ${errText}`);
        throw Object.assign(
            new Error(`Classification service error: ${response.status}`),
            { statusCode: response.status === 503 ? 503 : 502 }
        );
    }

    const result = await response.json();
    logger.debug(`[ClassifyService] Result: label=${result.label} conf=${result.confidence}`);
    return result;
}
