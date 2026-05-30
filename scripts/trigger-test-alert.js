import mqtt from 'mqtt';
import { PrismaClient } from '@prisma/client';
import { config } from 'dotenv';

config();

const prisma = new PrismaClient();
const BROKER_URL = process.env.MQTT_BROKER_URL || 'mqtt://localhost:1883';

async function run() {
    // 1. Dapatkan bin pertama dari database untuk mendapatkan nodeId-nya
    const bin = await prisma.bin.findFirst();
    if (!bin) {
        console.error('❌ Tidak ada bin di database. Jalankan `npm run seed` terlebih dahulu.');
        process.exit(1);
    }

    console.log(`\n📌 Mencoba menembak alert untuk Bin:`);
    console.log(`- ID: ${bin.id}`);
    console.log(`- Node ID: ${bin.nodeId}`);
    console.log(`- Area ID: ${bin.areaId || 'None'}`);

    // 2. Hubungkan ke MQTT Broker
    console.log(`\n🔌 Menghubungkan ke MQTT Broker di ${BROKER_URL}...`);
    const client = mqtt.connect(BROKER_URL, {
        clientId: `smartbin-tester-${Date.now()}`,
        clean: true,
    });

    client.on('connect', () => {
        console.log('✅ Terhubung ke MQTT broker!');
        
        // 3. Payload dengan nilai ekstrim melewati threshold default (Weight > 45, Volume > 85, Battery < 20)
        const payload = {
            weight: 55.5,    // Melebihi threshold default 45kg
            volume: 92.0,    // Melebihi threshold default 85%
            battery: 15.0,   // Di bawah threshold default 20%
            gas: 350.0,
            rssi: -50
        };

        const topic = `smartbin/${bin.nodeId}/sensor`;
        console.log(`\n📡 Mengirim payload simulasi ke topic [${topic}]:`);
        console.log(JSON.stringify(payload, null, 2));

        client.publish(topic, JSON.stringify(payload), { qos: 1 }, (err) => {
            if (err) {
                console.error('❌ Gagal mengirim MQTT message:', err.message);
            } else {
                console.log('\n🚀 Payload berhasil dikirim via MQTT!');
                console.log('Silakan periksa log terminal backend (npm run dev) untuk melihat alert yang terbuat.');
                console.log('Serta periksa websocket client atau halaman dashboard kamu!');
            }
            client.end();
        });
    });

    client.on('error', (err) => {
        console.error('❌ MQTT Broker Connection Error:', err.message);
        console.log('\n💡 Tips: Pastikan Mosquitto / MQTT Broker lokal Anda sudah menyala.');
        client.end();
    });
}

run()
    .catch(console.error)
    .finally(() => prisma.$disconnect());
