import { prisma } from '../config/db.js';

const LABELS = ['organik', 'anorganik', 'b3', 'unknown'];

/**
 * Agregasi jenis sampah hasil pemilah.
 * - Jumlah (count) diambil dari tabel `classifications` (tiap deteksi).
 * - Berat (weightKg) diambil dari tabel `deposits` (label + weight).
 * @param {{from?:string,to?:string,binId?:string,areaId?:string}} opts
 */
export async function getClassificationSummary({ from, to, binId, areaId } = {}) {
    // Filter bersama untuk classifications & deposits (kedua tabel punya
    // createdAt, binId, dan relasi bin.areaId).
    const where = {};
    if (from || to) {
        where.createdAt = {};
        if (from) where.createdAt.gte = new Date(from);
        if (to) where.createdAt.lte = new Date(to);
    }
    if (binId) where.binId = binId;
    if (areaId) where.bin = { areaId };

    const [clsGrouped, depGrouped] = await Promise.all([
        prisma.classification.groupBy({ by: ['label'], where, _count: { _all: true } }),
        prisma.deposit.groupBy({ by: ['label'], where, _sum: { weight: true } }),
    ]);

    const countByLabel = Object.fromEntries(clsGrouped.map((g) => [g.label, g._count._all]));
    const weightByLabel = Object.fromEntries(depGrouped.map((g) => [g.label, g._sum.weight || 0]));

    const total = clsGrouped.reduce((s, g) => s + g._count._all, 0);
    const totalWeightKg = depGrouped.reduce((s, g) => s + (g._sum.weight || 0), 0);

    const byLabel = LABELS.map((label) => {
        const count = countByLabel[label] || 0;
        return {
            label,
            count,
            weightKg: Math.round((weightByLabel[label] || 0) * 10) / 10,
            percentage: total ? Math.round((count / total) * 100) : 0,
        };
    });

    const top = byLabel.reduce((a, b) => (b.count > a.count ? b : a), byLabel[0]);

    return {
        total,
        totalWeightKg: Math.round(totalWeightKg * 10) / 10,
        byLabel,
        mostCommon: total ? top.label : null,
    };
}

/**
 * Jumlah klasifikasi (sampah terpilah) per hari, N hari terakhir, KONTINU
 * (selalu N titik, hari ini paling kanan). Untuk grafik "Sampah Terpilah 7 Hari".
 * Data ini reliable (dari kamera) — beda dari berat yang bergantung load cell.
 * @param {number} days
 * @returns {Promise<Array<{day:string, count:number}>>}
 */
export async function getWeeklyClassifications(days = 7) {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - (days - 1));

    const rows = await prisma.classification.findMany({
        where: { createdAt: { gte: start } },
        select: { createdAt: true },
    });

    const map = new Map();
    for (const r of rows) {
        const dt = new Date(r.createdAt);
        const key = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
        map.set(key, (map.get(key) ?? 0) + 1);
    }

    const out = [];
    for (let i = days - 1; i >= 0; i--) {
        const d = new Date();
        d.setHours(0, 0, 0, 0);
        d.setDate(d.getDate() - i);
        const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        out.push({ day: key, count: map.get(key) ?? 0 });
    }
    return out;
}

/**
 * Daftar klasifikasi TERBARU (tiap deteksi kamera/pemilah) — untuk panel
 * "Jenis Sampah Terdeteksi" di Analitik. Beda dari deposit: classification
 * tercatat SETIAP deteksi (tak butuh pairing berat).
 * @param {{binId?:string, from?:string, to?:string, limit?:number}} opts
 */
export async function getRecentClassifications({ binId, from, to, limit = 20 } = {}) {
    const where = {};
    if (binId) where.binId = binId;
    if (from || to) {
        where.createdAt = {};
        if (from) where.createdAt.gte = new Date(from);
        if (to) where.createdAt.lte = new Date(to);
    }
    return prisma.classification.findMany({
        where,
        orderBy: { createdAt: 'desc' },
        take: Math.min(Math.max(1, Number(limit) || 20), 100),
        include: { bin: { select: { nodeId: true, location: true } } },
    });
}
