import admin from 'firebase-admin';
import fs from 'fs';
import path from 'path';
import { env } from '../config/env.js';
import { findAllPetugas } from '../models/user.model.js';
import { logger } from '../utils/logger.js';

let firebaseInitialized = false;

function initFirebase() {
    if (firebaseInitialized) return;
    if (!env.FIREBASE_CREDENTIALS_PATH) {
        logger.warn('[Notify] FIREBASE_CREDENTIALS_PATH not set. Push notifications disabled.');
        return;
    }

    try {
        const credPath = path.resolve(env.FIREBASE_CREDENTIALS_PATH);
        const raw = fs.readFileSync(credPath, 'utf-8');
        const credentials = JSON.parse(raw);
        admin.initializeApp({
            credential: admin.credential.cert(credentials),
        });
        firebaseInitialized = true;
        logger.info('[Notify] Firebase Admin initialized');
    } catch (err) {
        logger.error('[Notify] Firebase initialization failed:', err.message);
    }
}

initFirebase();

/**
 * Send FCM push notification to all PETUGAS users in the bin's area
 * @param {{ type: string, message: string, binId: string, id: string, bin?: { areaId: string } }} alert
 */
export async function sendPushNotif(alert) {
    if (!firebaseInitialized) {
        logger.warn('[Notify] Firebase not initialized — skipping push notification');
        return;
    }

    const areaId = alert.bin?.areaId || null;
    const petugas = await findAllPetugas(areaId);
    if (petugas.length === 0) {
        logger.debug('[Notify] No petugas with device tokens found');
        return;
    }

    const tokens = petugas.map((p) => p.deviceToken).filter(Boolean);
    if (tokens.length === 0) return;

    const message = {
        notification: {
            title: `🚨 SmartBin Alert: ${alert.type.replace('_', ' ')}`,
            body: alert.message,
        },
        data: {
            alertId: alert.id,
            binId: alert.binId,
            type: alert.type,
        },
        tokens,
    };

    try {
        const response = await admin.messaging().sendEachForMulticast(message);
        logger.info(`[Notify] Push notification sent: ${response.successCount}/${tokens.length} success`);

        if (response.failureCount > 0) {
            response.responses.forEach((resp, idx) => {
                if (!resp.success) {
                    logger.warn(`[Notify] Failed token ${tokens[idx]}: ${resp.error?.message}`);
                }
            });
        }
    } catch (err) {
        logger.error('[Notify] FCM sendEachForMulticast error:', err.message);
        throw err;
    }
}
