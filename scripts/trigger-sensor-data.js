import { PrismaClient } from '@prisma/client';
import { handleSensorData } from '../src/mqtt/handlers/sensorData.js';
import { handleStatusData } from '../src/mqtt/handlers/statusData.js';
import { config } from 'dotenv';
import { redisClient, connectRedis } from '../src/config/redis.js';

config();

const prisma = new PrismaClient();

async function run() {
    // 0. Hubungkan ke Redis (agar redisClient ter-inisialisasi)
    try {
        await connectRedis();
    } catch (e) {
        console.warn('⚠️ Gagal terhubung ke Redis:', e.message);
    }
    // 1. Parsing Command Line Arguments (Contoh: node scripts/trigger-sensor-data.js bin-002 85)
    const targetNodeId = process.argv[2];
    const customVolume = process.argv[3] ? parseFloat(process.argv[3]) : null;

    let bin;
    if (targetNodeId) {
        bin = await prisma.bin.findUnique({ where: { nodeId: targetNodeId } });
        if (!bin) {
            console.error(`\n❌ Bin dengan Node ID "${targetNodeId}" tidak ditemukan.`);
            console.log('\nDaftar Bin yang tersedia di database Anda:');
            const allBins = await prisma.bin.findMany();
            allBins.forEach((b) => {
                console.log(`  👉 Node ID: "${b.nodeId}" | Location: "${b.location}"`);
            });
            console.log('\n💡 Gunakan salah satu Node ID di atas. Contoh: node scripts/trigger-sensor-data.js bin-002');
            process.exit(1);
        }
    } else {
        bin = await prisma.bin.findFirst();
        if (!bin) {
            console.error('❌ Tidak ada bin di database. Jalankan `npm run seed` terlebih dahulu.');
            process.exit(1);
        }
        console.log(`\n💡 Info: Kamu menggunakan bin default pertama ("${bin.nodeId}").`);
        console.log(`   Untuk mengecek bin lain, ketik Node ID-nya. Contoh: node scripts/trigger-sensor-data.js bin-002`);
    }

    // Tentukan volume (kapasitas) — default 75% jika tidak ditentukan
    const targetVolume = customVolume !== null ? customVolume : 75.0;

    console.log(`\n📌 Mensimulasikan Data Sensor Masuk untuk Bin:`);
    console.log(`- ID: ${bin.id}`);
    console.log(`- Node ID: ${bin.nodeId}`);
    console.log(`- Location: ${bin.location}`);

    // Input persentase kapasitas yang ingin kamu test
    const payload = {
        weight: targetVolume > 80 ? 48.5 : 35.2, // sesuaikan berat jika kapasitas tinggi
        volume: targetVolume,
        battery: 88.0,
        gas: 120.0,
        rssi: -65
    };

    console.log(`\n📡 Mengirim data telemetri ke handleSensorData():`);
    console.log(JSON.stringify(payload, null, 2));

    try {
        // 2. Set status bin menjadi 'online' di Redis
        await handleStatusData(bin.nodeId, { status: 'online' });

        // 3. Panggil handler data sensor utama
        await handleSensorData(bin.nodeId, payload);
        
        console.log('\n✅ Data sensor & Status berhasil diproses oleh Backend!');
        console.log('  1. Status di-set ONLINE di Redis (TTL 3 menit)');
        console.log('  2. Tersimpan di database PostgreSQL (tabel sensor_logs)');
        console.log('  3. Tercache di Redis sebagai data terbaru');
        console.log('  4. Pengecekan threshold & alert selesai');
        console.log('  5. Broadcast terkirim via WebSocket');

        // 4. Verifikasi apakah data terbaru berhasil ter-cache di Redis
        try {
            if (redisClient && redisClient.status === 'ready') {
                const cached = await redisClient.get(`bin:${bin.nodeId}:latest`);
                const statusCached = await redisClient.get(`bin:${bin.nodeId}:status`);
                if (cached) {
                    console.log(`\n🔴 Data di Redis Cache (Realtime):`);
                    console.log(JSON.stringify(JSON.parse(cached), null, 2));
                }
                console.log(`🟢 Status Realtime di Redis Cache: "${statusCached}"`);
            } else {
                console.log('\n⚠️ Redis server mati atau belum terhubung. Kapasitas realtime di API /bins mungkin akan kosong/offline jika Redis tidak aktif.');
            }
        } catch (e) {
            console.log('\n⚠️ Gagal membaca dari Redis:', e.message);
        }

        // 4. Verifikasi apakah log tersimpan di PostgreSQL
        const latestLog = await prisma.sensorLog.findFirst({
            where: { binId: bin.id },
            orderBy: { createdAt: 'desc' },
        });

        if (latestLog) {
            console.log(`\n🐘 Log Terakhir di PostgreSQL (SensorLog):`);
            console.log(`  - Log ID: ${latestLog.id}`);
            console.log(`  - Volume (Kapasitas): ${latestLog.volume}%`);
            console.log(`  - Weight: ${latestLog.weight}kg`);
            console.log(`  - Timestamp: ${latestLog.createdAt.toISOString()}`);
            console.log('\n🚀 SUKSES! Kapasitas/Persentase terisi sekarang sudah berhasil diperbarui di database dan cache!');
        }

    } catch (err) {
        console.error('❌ Gagal memproses data sensor:', err);
    }
}

run()
    .catch(console.error)
    .finally(async () => {
        await prisma.$disconnect();
        // Close redis client if open so process can exit cleanly
        try {
            redisClient.disconnect();
        } catch {}
    });
