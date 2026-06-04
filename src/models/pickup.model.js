import { prisma } from '../config/db.js';

const binSelect = { select: { nodeId: true, location: true, lat: true, lng: true } };
const petugasSelect = { select: { id: true, name: true, email: true } };

/**
 * Create a pickup record (the "Selesai" button checkpoint)
 * @param {{ binId, petugasId, areaId?, alertId?, completedLat?, completedLng? }} data
 */
export async function createPickup(data) {
    return prisma.pickup.create({
        data,
        include: { bin: binSelect, petugas: petugasSelect },
    });
}

/**
 * Hitung pickup yang sudah terkonfirmasi sensor (SELESAI) untuk seorang petugas
 * di sebuah area pada tanggal tertentu. Dipakai untuk menentukan jadwal SELESAI.
 * @param {string} petugasId
 * @param {string|null} areaId
 * @param {Date} date
 */
export async function countConfirmedPickups(petugasId, areaId, date) {
    const start = new Date(date);
    start.setHours(0, 0, 0, 0);
    const end = new Date(start);
    end.setDate(end.getDate() + 1);

    return prisma.pickup.count({
        where: {
            petugasId,
            areaId: areaId ?? null,
            status: 'SELESAI',
            completedAt: { gte: start, lt: end },
        },
    });
}

/**
 * List pickups (paginated, newest first), with optional area scoping & status filter.
 * PETUGAS hanya melihat pickup di areanya.
 * @param {object} user
 * @param {{ status?: string, binId?: string }} filters
 * @param {number} limit
 * @param {number} page
 */
export async function findAllPickups(user, { status, binId } = {}, limit = 50, page = 1) {
    const where = {};
    if (status) where.status = status;
    if (binId) where.binId = binId;

    if (user && user.role === 'PETUGAS' && user.areaId) {
        where.areaId = user.areaId;
    }

    const skip = (page - 1) * limit;
    const [items, total] = await Promise.all([
        prisma.pickup.findMany({
            where,
            orderBy: { completedAt: 'desc' },
            take: limit,
            skip,
            include: { bin: binSelect, petugas: petugasSelect },
        }),
        prisma.pickup.count({ where }),
    ]);
    return { items, total };
}

/**
 * Get a single pickup by id (with bin + petugas info)
 * @param {string} id
 */
export async function findPickupById(id) {
    return prisma.pickup.findUnique({
        where: { id },
        include: { bin: binSelect, petugas: petugasSelect },
    });
}

/**
 * Sensor checkpoint: tandai pickup yang masih MENUNGGU_SENSOR untuk sebuah bin
 * menjadi SELESAI saat sensor membaca bin sudah kosong.
 * Mengembalikan pickup yang di-update, atau null jika tidak ada yang menunggu.
 * @param {string} binId
 */
export async function confirmLatestPendingBySensor(binId) {
    const pending = await prisma.pickup.findFirst({
        where: { binId, status: 'MENUNGGU_SENSOR' },
        orderBy: { completedAt: 'desc' },
    });
    if (!pending) return null;

    return prisma.pickup.update({
        where: { id: pending.id },
        data: { status: 'SELESAI', sensorConfirmedAt: new Date() },
        include: { bin: binSelect, petugas: petugasSelect },
    });
}

/**
 * Konfirmasi manual: tandai pickup SELESAI tanpa nunggu sensor.
 * Fallback saat sensor error / tidak terbaca. Mencatat siapa yang menutup.
 * @param {string} id - id pickup
 * @param {string} userId - user (petugas/admin) yang menutup
 */
export async function confirmPickupManual(id, userId) {
    return prisma.pickup.update({
        where: { id },
        data: { status: 'SELESAI', manualConfirmedAt: new Date(), manualConfirmedById: userId },
        include: { bin: binSelect, petugas: petugasSelect },
    });
}
