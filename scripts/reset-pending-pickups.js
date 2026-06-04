import 'dotenv/config';
import { prisma } from '../src/config/db.js';

// Hapus semua pickup yang masih MENUNGGU_SENSOR (nyangkup dari tes / belum dikonfirmasi sensor).
// Setelah ini, bin yang tadinya ke-hide akan muncul lagi di tab "Perlu Pickup".
const pending = await prisma.pickup.findMany({
    where: { status: 'MENUNGGU_SENSOR' },
    include: { bin: { select: { nodeId: true } } },
});

console.log(`Ditemukan ${pending.length} pickup MENUNGGU_SENSOR:`);
for (const p of pending) console.log(`  - ${p.bin?.nodeId ?? p.binId} | ${p.id} | ${p.completedAt}`);

const result = await prisma.pickup.deleteMany({ where: { status: 'MENUNGGU_SENSOR' } });
console.log(`\nDihapus: ${result.count} record.`);

await prisma.$disconnect();
