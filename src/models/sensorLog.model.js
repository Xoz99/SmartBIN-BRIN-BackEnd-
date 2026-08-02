import { prisma } from '../config/db.js';

/**
 * Create a new sensor log entry
 * @param {{ binId, weight, volume, battery, rssi, wasteType? }} data
 */
export async function createSensorLog(data) {
    return prisma.sensorLog.create({ data });
}

/**
 * Berat PUNCAK per tong per hari (kg), dijumlah antar tong → total per hari.
 * Fallback grafik "Volume Sampah 7 Hari" saat snapshot zona & deposit kosong,
 * tapi load cell (berat_g→weight) di sensor_logs ada isinya. Berat sensor = berat
 * TONG saat itu; puncak per hari mewakili beban tong hari itu.
 * @param {number} days
 * @returns {Promise<Map<string, number>>} key="YYYY-MM-DD" (lokal), value=kg
 */
export async function maxWeightPerDay(days = 7) {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - (days - 1));

    const logs = await prisma.sensorLog.findMany({
        where: { createdAt: { gte: start }, weight: { gt: 0 } },
        select: { binId: true, weight: true, createdAt: true },
    });

    // day -> (binId -> berat maksimum)
    const perDayBin = new Map();
    for (const l of logs) {
        const dt = new Date(l.createdAt);
        const key = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
        if (!perDayBin.has(key)) perDayBin.set(key, new Map());
        const binMap = perDayBin.get(key);
        binMap.set(l.binId, Math.max(binMap.get(l.binId) ?? 0, l.weight ?? 0));
    }
    // jumlah berat puncak semua tong per hari
    const map = new Map();
    for (const [day, binMap] of perDayBin) {
        let sum = 0;
        for (const w of binMap.values()) sum += w;
        map.set(day, sum);
    }
    return map;
}

/**
 * Get paginated sensor logs for a bin
 * @param {string} binId
 * @param {number} limit
 * @param {number} page
 */
export async function findLogsByBinId(binId, limit = 50, page = 1, { from, to, transport } = {}) {
    const skip = (page - 1) * limit;

    // Filter opsional: rentang tanggal (createdAt) + transport (lora/http).
    // Dipakai panel analitik agar riwayat sinyal ikut periode terpilih (bukan
    // selalu "N terakhir") dan LoRa tidak terdorong keluar oleh HTTP yang rapat.
    const where = { binId };
    const createdAt = {};
    if (from) { const d = new Date(from); if (!Number.isNaN(d.getTime())) createdAt.gte = d; }
    if (to)   { const d = new Date(to);   if (!Number.isNaN(d.getTime())) createdAt.lte = d; }
    if (createdAt.gte || createdAt.lte) where.createdAt = createdAt;
    if (transport) where.transport = transport;

    const [items, total] = await Promise.all([
        prisma.sensorLog.findMany({
            where,
            orderBy: { createdAt: 'desc' },
            take: limit,
            skip,
        }),
        prisma.sensorLog.count({ where }),
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

