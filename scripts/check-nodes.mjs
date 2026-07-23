// Cek node bin-yono & bin-003 di DB (read-only diagnostic).
import { PrismaClient } from '@prisma/client';
const prisma = new PrismaClient();

async function main() {
    for (const nodeId of ['bin-yono', 'bin-003']) {
        const bin = await prisma.bin.findUnique({ where: { nodeId } });
        if (!bin) {
            console.log(`[${nodeId}] TIDAK terdaftar di tabel bins.`);
            continue;
        }
        const [logs, classs] = await Promise.all([
            prisma.sensorLog.count({ where: { binId: bin.id } }),
            prisma.classification.count({ where: { binId: bin.id } }),
        ]);
        console.log(`[${nodeId}] TERDAFTAR (id=${bin.id}) | sensorLogs=${logs} | classifications=${classs}`);
    }
}
main().catch((e) => { console.error('[ERROR]', e.message); process.exit(1); })
      .finally(() => prisma.$disconnect());
