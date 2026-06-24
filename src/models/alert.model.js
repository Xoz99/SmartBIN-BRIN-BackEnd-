import { prisma } from '../config/db.js';

/**
 * Create a new alert
 * @param {{ binId, type, message }} data
 */
export async function createAlert(data) {
    return prisma.alert.create({ data });
}

/**
 * Get all alerts, optionally filter by resolved status and by user area
 * @param {object} user - The user requesting the alerts
 * @param {{ resolved?: boolean }} filters
 * @param {number} limit
 * @param {number} page
 */
export async function findAllAlerts(user, { resolved } = {}, limit = 50, page = 1) {
    const where = {};
    if (typeof resolved !== 'undefined') where.resolved = resolved;
    // Filter area petugas dinonaktifkan — petugas melihat semua alert.

    const skip = (page - 1) * limit;
    const [items, total] = await Promise.all([
        prisma.alert.findMany({
            where,
            orderBy: { createdAt: 'desc' },
            take: limit,
            skip,
            include: { bin: { select: { nodeId: true, location: true } } },
        }),
        prisma.alert.count({ where }),
    ]);
    return { items, total };
}

/**
 * Get alerts for a specific bin
 * @param {string} binId
 * @param {{ resolved?: boolean }} filters
 */
export async function findAlertsByBinId(binId, { resolved } = {}) {
    const where = { binId };
    if (typeof resolved !== 'undefined') where.resolved = resolved;
    return prisma.alert.findMany({
        where,
        orderBy: { createdAt: 'desc' },
    });
}

/**
 * Mark an alert as resolved
 * @param {string} id
 */
export async function resolveAlert(id) {
    return prisma.alert.update({
        where: { id },
        data: { resolved: true, resolvedAt: new Date() },
    });
}

/**
 * Tandai SEMUA alert aktif untuk satu bin jadi resolved (dipakai saat pickup selesai).
 * @param {string} binId
 * @returns {Promise<number>} jumlah alert yang diselesaikan
 */
export async function resolveAllAlertsForBin(binId) {
    const res = await prisma.alert.updateMany({
        where: { binId, resolved: false },
        data: { resolved: true, resolvedAt: new Date() },
    });
    return res.count;
}

/**
 * Check if there's an unresolved alert of the given type for a bin
 * (to avoid duplicate alerts)
 * @param {string} binId
 * @param {string} type
 */
export async function findActiveAlert(binId, type) {
    return prisma.alert.findFirst({
        where: { binId, type, resolved: false },
        orderBy: { createdAt: 'desc' },
    });
}
