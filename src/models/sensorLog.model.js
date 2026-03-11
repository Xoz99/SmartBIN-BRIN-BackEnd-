import { prisma } from '../config/db.js';

/**
 * Create a new sensor log entry
 * @param {{ binId, weight, volume, battery, rssi, wasteType? }} data
 */
export async function createSensorLog(data) {
    return prisma.sensorLog.create({ data });
}

/**
 * Get paginated sensor logs for a bin
 * @param {string} binId
 * @param {number} limit
 * @param {number} page
 */
export async function findLogsByBinId(binId, limit = 50, page = 1) {
    const skip = (page - 1) * limit;
    const [items, total] = await Promise.all([
        prisma.sensorLog.findMany({
            where: { binId },
            orderBy: { createdAt: 'desc' },
            take: limit,
            skip,
        }),
        prisma.sensorLog.count({ where: { binId } }),
    ]);
    return { items, total };
}

/**
 * Get the most recent sensor log for a bin
 * @param {string} binId
 */
export async function findLatestByBinId(binId) {
    return prisma.sensorLog.findFirst({
        where: { binId },
        orderBy: { createdAt: 'desc' },
    });
}

/**
 * Update wasteType on a specific log (after image classification)
 * @param {string} logId
 * @param {string} wasteType
 */
export async function updateWasteType(logId, wasteType) {
    return prisma.sensorLog.update({
        where: { id: logId },
        data: { wasteType },
    });
}
