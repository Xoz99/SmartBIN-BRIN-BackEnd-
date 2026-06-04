import { createPickup, findAllPickups, findPickupById, confirmLatestPendingBySensor, confirmPickupManual, countConfirmedPickups } from '../models/pickup.model.js';
import { findBinById } from '../models/bin.model.js';
import { findActiveAlert } from '../models/alert.model.js';
import { findActiveScheduleFor, setScheduleStatus } from '../models/schedule.model.js';
import { broadcast } from '../websocket/ws.js';
import { logger } from '../utils/logger.js';

/**
 * Auto-update status jadwal petugas berdasarkan aktivitas pickup.
 * - phase 'start'   : jadwal PENDING → PROSES (petugas mulai mengangkut)
 * - phase 'confirm' : jadwal → SELESAI jika jumlah pickup terkonfirmasi >= binTarget
 */
async function syncSchedule(petugasId, areaId, date, phase) {
    try {
        const sched = await findActiveScheduleFor(petugasId, areaId, date);
        if (!sched) return;

        if (phase === 'confirm') {
            const confirmed = await countConfirmedPickups(petugasId, areaId, sched.date);
            const target = sched.binTarget || 0;
            const next = target > 0 && confirmed >= target ? 'SELESAI' : 'PROSES';
            if (sched.status !== next) {
                await setScheduleStatus(sched.id, next);
                broadcast('SCHEDULE_UPDATED', { scheduleId: sched.id, petugasId, status: next });
            }
        } else if (sched.status === 'PENDING') {
            await setScheduleStatus(sched.id, 'PROSES');
            broadcast('SCHEDULE_UPDATED', { scheduleId: sched.id, petugasId, status: 'PROSES' });
        }
    } catch (e) {
        logger.warn(`[ScheduleSync] gagal update jadwal: ${e.message}`);
    }
}

/**
 * Petugas menekan tombol "Selesai" setelah mengambil sampah.
 * Membuat record Pickup (checkpoint: siapa/kapan/GPS), status MENUNGGU_SENSOR.
 *
 * @param {string} binId
 * @param {object} user - req.user (petugas/admin yang menekan)
 * @param {{ lat?: number, lng?: number }} location
 * @returns {Promise<{ pickup?: object, error?: { message: string, status: number } }>}
 */
export async function completePickup(binId, user, { lat, lng } = {}) {
    const bin = await findBinById(binId);
    if (!bin) return { error: { message: 'Bin not found', status: 404 } };

    // Area ownership — PETUGAS hanya boleh menyelesaikan pickup di areanya
    if (user.role === 'PETUGAS' && user.areaId && bin.areaId && bin.areaId !== user.areaId) {
        return { error: { message: 'Forbidden: bin is not in your area', status: 403 } };
    }

    // Kaitkan dengan alert "penuh" yang sedang aktif (jika ada)
    const activeAlert =
        (await findActiveAlert(binId, 'FULL_VOLUME')) ||
        (await findActiveAlert(binId, 'FULL_WEIGHT'));

    const pickup = await createPickup({
        binId,
        petugasId: user.id,
        areaId: bin.areaId,
        alertId: activeAlert?.id ?? null,
        completedLat: typeof lat === 'number' ? lat : null,
        completedLng: typeof lng === 'number' ? lng : null,
    });

    logger.info(`[PickupService] ✅ ${user.email} menyelesaikan pickup bin ${bin.nodeId} (menunggu konfirmasi sensor)`);

    broadcast('PICKUP_COMPLETED', {
        pickupId: pickup.id,
        binId,
        nodeId: bin.nodeId,
        petugasId: user.id,
        status: pickup.status,
        completedAt: pickup.completedAt,
        areaId: bin.areaId,
    });

    // Auto-update jadwal: PENDING → PROSES
    await syncSchedule(user.id, bin.areaId, new Date(), 'start');

    return { pickup };
}

/**
 * Sensor checkpoint — dipanggil dari alert.service saat alert FULL auto-resolve.
 * Menandai pickup MENUNGGU_SENSOR untuk bin tsb menjadi SELESAI.
 * @param {string} binId
 * @param {string} nodeId
 */
export async function confirmPickupBySensor(binId, nodeId) {
    const pickup = await confirmLatestPendingBySensor(binId);
    if (!pickup) return null;

    logger.info(`[PickupService] 🛰️ Sensor mengkonfirmasi pickup bin ${nodeId} SELESAI (pickup ${pickup.id})`);

    broadcast('PICKUP_CONFIRMED', {
        pickupId: pickup.id,
        binId,
        nodeId,
        petugasId: pickup.petugasId,
        status: pickup.status,
        sensorConfirmedAt: pickup.sensorConfirmedAt,
        areaId: pickup.areaId,
    });

    // Auto-update jadwal: SELESAI jika target bin tercapai
    await syncSchedule(pickup.petugasId, pickup.areaId, pickup.completedAt || new Date(), 'confirm');

    return pickup;
}

/**
 * Konfirmasi manual oleh petugas/admin — fallback saat sensor error.
 * Menandai pickup MENUNGGU_SENSOR menjadi SELESAI tanpa nunggu sensor.
 * @param {string} id - id pickup
 * @param {object} user - req.user (yang menekan)
 * @returns {Promise<{ pickup?: object, error?: { message: string, status: number } }>}
 */
export async function manualConfirmPickup(id, user) {
    const pickup = await findPickupById(id);
    if (!pickup) return { error: { message: 'Pickup not found', status: 404 } };
    if (pickup.status === 'SELESAI') return { error: { message: 'Pickup sudah selesai', status: 409 } };

    // Area ownership — PETUGAS hanya boleh menutup pickup di areanya
    if (user.role === 'PETUGAS' && user.areaId && pickup.areaId && pickup.areaId !== user.areaId) {
        return { error: { message: 'Forbidden: pickup is not in your area', status: 403 } };
    }

    const updated = await confirmPickupManual(pickup.id, user.id);

    logger.info(`[PickupService] ✋ ${user.email} mengonfirmasi MANUAL pickup bin ${updated.bin?.nodeId ?? updated.binId} SELESAI (pickup ${updated.id})`);

    broadcast('PICKUP_CONFIRMED', {
        pickupId: updated.id,
        binId: updated.binId,
        nodeId: updated.bin?.nodeId,
        petugasId: updated.petugasId,
        status: updated.status,
        sensorConfirmedAt: updated.sensorConfirmedAt,
        manualConfirmedAt: updated.manualConfirmedAt,
        areaId: updated.areaId,
    });

    // Auto-update jadwal: SELESAI jika target bin tercapai
    await syncSchedule(updated.petugasId, updated.areaId, updated.completedAt || new Date(), 'confirm');

    return { pickup: updated };
}

/**
 * List pickups (admin: semua; petugas: areanya)
 */
export async function getPickups(user, filters, limit, page) {
    return findAllPickups(user, filters, limit, page);
}

/**
 * Detail satu pickup
 */
export async function getPickupById(id) {
    return findPickupById(id);
}
