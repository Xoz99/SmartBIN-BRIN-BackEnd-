// One-off: daftarkan node Raspi "bin-003" ke tabel bins (idempotent).
// Jalankan dari root project:  node -r dotenv/config scripts/register-bin-003.mjs
import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

const NODE_ID = 'bin-003';

async function main() {
    const bin = await prisma.bin.upsert({
        where: { nodeId: NODE_ID },
        update: {}, // sudah ada → biarkan apa adanya
        create: {
            nodeId: NODE_ID,
            location: 'Node Raspi bin-003',
            lat: 0,
            lng: 0,
        },
    });
    console.log('[OK] Bin siap:', {
        id: bin.id,
        nodeId: bin.nodeId,
        location: bin.location,
        lat: bin.lat,
        lng: bin.lng,
    });

    const all = await prisma.bin.findMany({
        select: { nodeId: true, location: true },
        orderBy: { createdAt: 'asc' },
    });
    console.log('[INFO] Semua bin terdaftar:', all);
}

main()
    .catch((e) => {
        console.error('[ERROR]', e.message);
        process.exit(1);
    })
    .finally(() => prisma.$disconnect());
