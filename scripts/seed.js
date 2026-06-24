/**
 * Seed script — creates sample data for local development / testing
 *
 * Usage: node scripts/seed.js
 */

import { PrismaClient } from '@prisma/client';
import bcrypt from 'bcryptjs';
import { config } from 'dotenv';

config();

const prisma = new PrismaClient();

async function main() {
    console.log('🌱 Seeding database...');

    // ─── Areas ────────────────────────────────────────────────────────────────
    const areaA = await prisma.area.upsert({
        where: { name: 'Kawasan Kampus A' },
        update: {},
        create: { name: 'Kawasan Kampus A' },
    });

    const areaB = await prisma.area.upsert({
        where: { name: 'Kawasan Kampus B' },
        update: {},
        create: { name: 'Kawasan Kampus B' },
    });
    console.log('✓ Areas created');

    // ─── Bins ─────────────────────────────────────────────────────────────────
    const bins = await Promise.all([
        prisma.bin.upsert({
            where: { nodeId: 'bin-001' },
            update: {},
            create: {
                nodeId: 'bin-001',
                location: 'Gedung A - Lantai 1',
                lat: -6.2088,
                lng: 106.8456,
                areaId: areaA.id,
            },
        }),
        prisma.bin.upsert({
            where: { nodeId: 'bin-002' },
            update: {},
            create: {
                nodeId: 'bin-002',
                location: 'Taman Kampus Utama',
                lat: -6.2095,
                lng: 106.8462,
                areaId: areaA.id,
            },
        }),
        prisma.bin.upsert({
            where: { nodeId: 'bin-003' },
            update: {},
            create: {
                nodeId: 'bin-003',
                location: 'Kantin Kampus B',
                lat: -6.2101,
                lng: 106.8470,
                areaId: areaB.id,
            },
        }),
    ]);
    console.log(`✓ ${bins.length} bins created/updated`);

    // ─── Users ────────────────────────────────────────────────────────────────
    const adminPassword = await bcrypt.hash('admin123', 12);
    const petugasPassword = await bcrypt.hash('petugas123', 12);

    const admin = await prisma.user.upsert({
        where: { email: 'admin@smartbin.local' },
        update: {},
        create: {
            name: 'Administrator',
            email: 'admin@smartbin.local',
            password: adminPassword,
            role: 'ADMIN',
        },
    });

    const petugas = await prisma.user.upsert({
        where: { email: 'petugas@smartbin.local' },
        update: {},
        create: {
            name: 'Petugas Kebersihan',
            email: 'petugas@smartbin.local',
            password: petugasPassword,
            role: 'PETUGAS',
            areaId: areaA.id,
        },
    });

    const petugasB = await prisma.user.upsert({
        where: { email: 'petugas_b@smartbin.local' },
        update: {},
        create: {
            name: 'Petugas Kampus B',
            email: 'petugas_b@smartbin.local',
            password: petugasPassword,
            role: 'PETUGAS',
            areaId: areaB.id,
        },
    });
    console.log(`✓ Users created — admin & petugas`);

    // ─── Sample sensor logs ───────────────────────────────────────────────────
    const now = new Date();
    for (const bin of bins) {
        for (let i = 0; i < 10; i++) {
            const ts = new Date(now.getTime() - i * 5 * 60 * 1000); // 5 min intervals
            await prisma.sensorLog.create({
                data: {
                    binId: bin.id,
                    weight: parseFloat((Math.random() * 40).toFixed(2)),
                    volume: parseFloat((Math.random() * 80).toFixed(2)),
                    battery: parseFloat((60 + Math.random() * 40).toFixed(2)),
                    rssi: -Math.floor(60 + Math.random() * 30),
                    createdAt: ts,
                },
            });
        }
    }
    console.log('✓ 10 sensor logs per bin created');

    console.log('\n🎉 Seeding complete!\n');
    console.log('  Admin login:       admin@smartbin.local / admin123');
    console.log('  Petugas A login:   petugas@smartbin.local / petugas123');
    console.log('  Petugas B login:   petugas_b@smartbin.local / petugas123\n');
}

main()
    .catch((e) => { console.error(e); process.exit(1); })
    .finally(() => prisma.$disconnect());
