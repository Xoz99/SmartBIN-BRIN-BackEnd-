// Daftarkan sebuah node ke tabel bins (idempotent).
// Pakai:  node -r dotenv/config scripts/register-bin.mjs <nodeId> ["Lokasi"]
import { PrismaClient } from '@prisma/client';
const prisma = new PrismaClient();

const nodeId   = process.argv[2];
const location = process.argv[3] || `Node ${process.argv[2]}`;

if (!nodeId) {
    console.error('Usage: node -r dotenv/config scripts/register-bin.mjs <nodeId> ["Lokasi"]');
    process.exit(1);
}

async function main() {
    const bin = await prisma.bin.upsert({
        where: { nodeId },
        update: {},                                  // sudah ada → biarkan
        create: { nodeId, location, lat: 0, lng: 0 },
    });
    console.log('[OK] Bin siap:', {
        id: bin.id, nodeId: bin.nodeId, location: bin.location, lat: bin.lat, lng: bin.lng,
    });
    const all = await prisma.bin.findMany({
        select: { nodeId: true, location: true }, orderBy: { createdAt: 'asc' },
    });
    console.log('[INFO] Semua bin terdaftar:', all);
}
main().catch((e) => { console.error('[ERROR]', e.message); process.exit(1); })
      .finally(() => prisma.$disconnect());
