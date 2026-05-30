import { PrismaClient } from '@prisma/client';
import { checkThreshold } from '../src/services/alert.service.js';
import { config } from 'dotenv';

config();

const prisma = new PrismaClient();

async function run() {
    // 1. Dapatkan bin pertama dari database
    const bin = await prisma.bin.findFirst();
    if (!bin) {
        console.error('❌ Tidak ada bin di database. Jalankan `npm run seed` terlebih dahulu.');
        process.exit(1);
    }

    console.log(`\n📌 Mencoba menembak alert secara langsung (bypass MQTT) untuk Bin:`);
    console.log(`- ID: ${bin.id}`);
    console.log(`- Node ID: ${bin.nodeId}`);
    console.log(`- Area ID: ${bin.areaId || 'None'}`);

    const payload = {
        weight: 55.5,    // Melebihi threshold default 45kg
        volume: 92.0,    // Melebihi threshold default 85%
        battery: 15.0,   // Di bawah threshold default 20%
        gas: 350.0,
    };

    console.log(`\n⚙️ Memanggil checkThreshold() secara langsung dengan data:`);
    console.log(JSON.stringify(payload, null, 2));

    try {
        // 2. Jalankan checkThreshold secara langsung
        await checkThreshold(bin.nodeId, bin.id, payload);
        
        console.log('\n✅ checkThreshold() berhasil dieksekusi!');
        
        // 3. Verifikasi apakah alert berhasil terbuat di database
        const activeAlerts = await prisma.alert.findMany({
            where: {
                binId: bin.id,
                resolved: false,
            },
            orderBy: { createdAt: 'desc' },
            take: 3,
        });

        console.log(`\n📊 Daftar alert aktif terbaru untuk bin ini di database:`);
        if (activeAlerts.length === 0) {
            console.log('  (Tidak ada alert aktif)');
        } else {
            activeAlerts.forEach((alert) => {
                console.log(`  - [${alert.type}] ${alert.message} (${alert.createdAt.toISOString()})`);
            });
            console.log('\n🚀 SUKSES! Pembuatan alert otomatis bekerja dengan sempurna!');
        }

    } catch (err) {
        console.error('❌ Gagal menjalankan test trigger:', err);
    }
}

run()
    .catch(console.error)
    .finally(() => prisma.$disconnect());
