import { prisma } from '../config/db.js';

/**
 * Get all bins, optionally filtered by area
 * @param {object} user - The user requesting the bins
 */
export async function findAllBins(user) {
    const where = {};
    if (user && user.role === 'PETUGAS' && user.areaId) {
        where.areaId = user.areaId;
    }

    return prisma.bin.findMany({
        where,
        orderBy: { createdAt: 'asc' },
    });
}

/**
 * Get a bin by primary key
 * @param {string} id
 */
export async function findBinById(id) {
    return prisma.bin.findUnique({ where: { id } });
}

/**
 * Get a bin by nodeId (ESP32 unique identifier)
 * @param {string} nodeId
 */
export async function findBinByNodeId(nodeId) {
    return prisma.bin.findUnique({ where: { nodeId } });
}

/**
 * Update bin threshold metadata (stored in Redis, but we log it on the bin record if needed)
 * This is a no-op on the DB model — thresholds are stored in Redis by the service layer.
 * Kept here for completeness in case you want to persist them to DB later.
 * @param {string} id
 * @param {{ location?: string }} data
 */
export async function updateBin(id, data) {
    return prisma.bin.update({ where: { id }, data });
}

/**
 * Create a new bin
 * @param {{ nodeId, location, lat, lng, areaId? }} data
 */
export async function createBin(data) {
    return prisma.bin.create({ data });
}

/**
 * Delete a bin by primary key
 * @param {string} id
 */
export async function deleteBin(id) {
    return prisma.bin.delete({ where: { id } });
}
