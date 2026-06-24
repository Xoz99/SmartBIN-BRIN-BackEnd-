import { prisma } from '../config/db.js';

/**
 * Catat satu pemusnahan (parsial) untuk sebuah kecamatan.
 * @param {{kecamatan:string, weightKg:number, confirmedBy?:string}} data
 */
export async function createDisposal({ kecamatan, weightKg, confirmedBy }) {
    return prisma.disposal.create({
        data: { kecamatan: kecamatan.toUpperCase(), weightKg, confirmedBy: confirmedBy ?? null },
    });
}

/**
 * Rekap pemusnahan per kecamatan. Pemusnahan LANGSUNG mengurangi sisa TPA & zona
 * (tanpa masa tunggu) — sesuai keputusan: begitu dikonfirmasi, langsung kurangi.
 * @returns {Promise<Map<string,{musnahKg:number}>>}
 */
export async function sumDisposalPerKecamatan() {
    const rows = await prisma.disposal.findMany({
        select: { kecamatan: true, weightKg: true },
    });

    const map = new Map();
    for (const r of rows) {
        const kec = (r.kecamatan || '').toUpperCase();
        const ex = map.get(kec) ?? { musnahKg: 0 };
        ex.musnahKg += r.weightKg ?? 0;
        map.set(kec, ex);
    }
    return map;
}

/**
 * Histori pemusnahan (opsional, untuk laporan).
 */
export async function listDisposals({ kecamatan, limit = 100 } = {}) {
    const where = {};
    if (kecamatan) where.kecamatan = kecamatan.toUpperCase();
    return prisma.disposal.findMany({
        where, orderBy: { createdAt: 'desc' }, take: limit,
    });
}
