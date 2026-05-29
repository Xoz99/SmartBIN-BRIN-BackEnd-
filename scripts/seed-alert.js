import { PrismaClient } from '@prisma/client';
const prisma = new PrismaClient();

async function main() {
    const bins = await prisma.bin.findMany();
    if (bins.length < 2) {
        console.log("Not enough bins to seed alerts.");
        return;
    }

    // Buat alert bohong-bohongan untuk bin pertama dan kedua
    await prisma.alert.createMany({
        data: [
            { 
                binId: bins[0].id, 
                type: 'FULL_VOLUME', 
                message: 'Kapasitas bin penuh (simulasi)', 
                resolved: false 
            },
            { 
                binId: bins[1].id, 
                type: 'FULL_WEIGHT', 
                message: 'Berat bin berlebih (simulasi)', 
                resolved: false 
            }
        ]
    });
    console.log("✅ 2 Dummy alerts berhasil dibuat! Silakan test ulang URL Route Optimization di Postman.");
}

main()
    .catch(console.error)
    .finally(() => prisma.$disconnect());
