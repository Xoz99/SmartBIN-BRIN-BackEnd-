-- AlterTable
ALTER TABLE "pickups" ADD COLUMN     "manualConfirmedAt" TIMESTAMP(3),
ADD COLUMN     "manualConfirmedById" TEXT;
