import { prisma } from '../config/db.js';

/**
 * Buat record deposit (setoran sampah hasil scan WARGA)
 * @param {{userId:string, binId:string, label:string, confidence?:number, weight?:number}} data
 */
export async function createDeposit(data) {
    return prisma.deposit.create({ data });
}

/**
 * Tempelkan berat (load cell) ke deposit terakhir di bin yang belum ada beratnya.
 * Dipakai saat sensor kirim berat setelah user scan (alur planku).
 * Hanya deposit dalam 10 menit terakhir agar tidak salah tempel.
 * @param {string} binId
 * @param {number} weight
 */
export async function attachWeightToLatestDeposit(binId, weight) {
    const tenMinAgo = new Date(Date.now() - 10 * 60 * 1000);
    const latest = await prisma.deposit.findFirst({
        where: { binId, weight: null, createdAt: { gte: tenMinAgo } },
        orderBy: { createdAt: 'desc' },
    });
    if (!latest) return null;
    return prisma.deposit.update({ where: { id: latest.id }, data: { weight } });
}

/**
 * Total berat (kg) setoran per bin untuk BULAN BERJALAN, lengkap koordinat bin.
 * Deposit tanpa berat dihitung default 1 kg agar scan-only tetap berkontribusi.
 * Dipakai untuk fold data aktual ke prediksi fill_pct per kecamatan.
 * @returns {Promise<Array<{binId:string, kg:number, lat:number, lng:number}>>}
 */
export async function sumWeightPerBinThisMonth() {
    const start = new Date();
    start.setDate(1);
    start.setHours(0, 0, 0, 0);

    const deposits = await prisma.deposit.findMany({
        where: { createdAt: { gte: start } },
        select: { binId: true, weight: true, bin: { select: { lat: true, lng: true } } },
    });

    const map = new Map();
    for (const d of deposits) {
        const ex = map.get(d.binId) ?? { binId: d.binId, kg: 0, lat: d.bin?.lat, lng: d.bin?.lng };
        ex.kg += d.weight ?? 1; // null weight (scan tanpa load cell) = 1 kg
        map.set(d.binId, ex);
    }
    return Array.from(map.values()).filter(b => b.lat != null && b.lng != null);
}

/**
 * Total berat (kg) setoran per HARI untuk N hari terakhir (untuk backfill grafik).
 * @returns {Promise<Map<string, number>>} key="YYYY-MM-DD", value=kg
 */
export async function sumWeightPerDay(days = 7) {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - (days - 1));

    const deposits = await prisma.deposit.findMany({
        where: { createdAt: { gte: start } },
        select: { weight: true, createdAt: true },
    });

    const map = new Map();
    for (const d of deposits) {
        const dt = new Date(d.createdAt);
        const key = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
        map.set(key, (map.get(key) ?? 0) + (d.weight ?? 1));
    }
    return map;
}

/**
 * Ambil histori deposit, opsional filter binId / userId
 * @param {{binId?:string, userId?:string, limit?:number}} filter
 */
export async function findDeposits({ binId, userId, nodeId, limit = 50 } = {}) {
    const where = {};
    if (binId) where.binId = binId;
    if (userId) where.userId = userId;
    if (nodeId) where.bin = { nodeId }; // filter pakai nodeId (mis. "bin-033")
    return prisma.deposit.findMany({
        where,
        orderBy: { createdAt: 'desc' },
        take: limit,
        include: {
            user: { select: { id: true, name: true } },
            bin: { select: { nodeId: true, location: true } },
        },
    });
}
