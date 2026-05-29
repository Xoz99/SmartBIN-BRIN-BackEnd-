import { PrismaClient } from '@prisma/client';
const prisma = new PrismaClient();
async function run() {
  const bins = await prisma.bin.findMany();
  if (bins.length >= 2) {
    await prisma.alert.createMany({
      data: [
        { binId: bins[0].id, type: 'FULL_VOLUME', message: 'Bin 1 is full', resolved: false },
        { binId: bins[1].id, type: 'FULL_WEIGHT', message: 'Bin 2 is heavy', resolved: false }
      ]
    });
    console.log('Dummy alerts created!');
  }
}
run().then(() => process.exit(0));
