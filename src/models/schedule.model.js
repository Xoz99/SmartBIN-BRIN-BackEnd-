import { prisma } from '../config/db.js';

const petugasSelect = { select: { id: true, name: true, email: true } };
const areaSelect = { select: { id: true, name: true } };
const include = { petugas: petugasSelect, area: areaSelect };

/**
 * Create a schedule
 * @param {{ petugasId, areaId?, date, startTime, endTime, truck?, binTarget?, note? }} data
 */
export async function createSchedule(data) {
    return prisma.schedule.create({ data, include });
}

/**
 * List schedules (paginated), with optional filters.
 * @param {{ petugasId?: string, status?: string, date?: string|Date }} filters
 */
export async function findAllSchedules({ petugasId, status, date } = {}, limit = 100, page = 1) {
    const where = {};
    if (petugasId) where.petugasId = petugasId;
    if (status) where.status = status;
    if (date) {
        const start = new Date(date);
        start.setHours(0, 0, 0, 0);
        const end = new Date(start);
        end.setDate(end.getDate() + 1);
        where.date = { gte: start, lt: end };
    }

    const skip = (page - 1) * limit;
    const [items, total] = await Promise.all([
        prisma.schedule.findMany({
            where,
            orderBy: [{ date: 'desc' }, { startTime: 'asc' }],
            take: limit,
            skip,
            include,
        }),
        prisma.schedule.count({ where }),
    ]);
    return { items, total };
}

export async function findScheduleById(id) {
    return prisma.schedule.findUnique({ where: { id }, include });
}

export async function updateSchedule(id, data) {
    return prisma.schedule.update({ where: { id }, data, include });
}

export async function deleteSchedule(id) {
    return prisma.schedule.delete({ where: { id } });
}

/**
 * Cari jadwal aktif (PENDING/PROSES) milik petugas di area & tanggal tertentu.
 * Dipakai untuk auto-update status saat pickup.
 * @param {string} petugasId
 * @param {string|null} areaId
 * @param {Date} date
 */
export async function findActiveScheduleFor(petugasId, areaId, date) {
    const start = new Date(date);
    start.setHours(0, 0, 0, 0);
    const end = new Date(start);
    end.setDate(end.getDate() + 1);

    return prisma.schedule.findFirst({
        where: {
            petugasId,
            areaId: areaId ?? null,
            date: { gte: start, lt: end },
            status: { in: ['PENDING', 'PROSES'] },
        },
        orderBy: { createdAt: 'desc' },
    });
}

export async function setScheduleStatus(id, status) {
    return prisma.schedule.update({ where: { id }, data: { status } });
}
