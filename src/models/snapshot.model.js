import { prisma } from '../config/db.js';

// "YYYY-MM-DD" lokal
function dayKey(date = new Date()) {
    const d = new Date(date);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${dd}`;
}

/**
 * Simpan/replace snapshot total volume zona untuk satu hari (idempoten).
 */
export async function upsertSnapshot(day, totalKg) {
    return prisma.zoneSnapshot.upsert({
        where: { day },
        update: { totalKg },
        create: { day, totalKg },
    });
}

/**
 * Ambil snapshot N hari terakhir (termasuk hari ini), urut tanggal naik.
 * @returns {Promise<Array<{day:string, totalKg:number}>>}
 */
export async function getRecentSnapshots(days = 7) {
    const rows = await prisma.zoneSnapshot.findMany({
        orderBy: { day: 'desc' },
        take: days,
        select: { day: true, totalKg: true },
    });
    return rows.reverse();
}

export { dayKey };
