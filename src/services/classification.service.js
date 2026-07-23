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
