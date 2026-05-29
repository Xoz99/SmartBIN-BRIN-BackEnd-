-- CreateEnum
CREATE TYPE "PickupStatus" AS ENUM ('MENUNGGU_SENSOR', 'SELESAI');

-- CreateTable
CREATE TABLE "pickups" (
    "id" TEXT NOT NULL,
    "binId" TEXT NOT NULL,
    "petugasId" TEXT NOT NULL,
    "areaId" TEXT,
    "alertId" TEXT,
    "status" "PickupStatus" NOT NULL DEFAULT 'MENUNGGU_SENSOR',
    "completedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completedLat" DOUBLE PRECISION,
    "completedLng" DOUBLE PRECISION,
    "sensorConfirmedAt" TIMESTAMP(3),
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "pickups_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "pickups_binId_idx" ON "pickups"("binId");

-- CreateIndex
CREATE INDEX "pickups_petugasId_idx" ON "pickups"("petugasId");

-- CreateIndex
CREATE INDEX "pickups_status_idx" ON "pickups"("status");

-- AddForeignKey
ALTER TABLE "pickups" ADD CONSTRAINT "pickups_binId_fkey" FOREIGN KEY ("binId") REFERENCES "bins"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "pickups" ADD CONSTRAINT "pickups_petugasId_fkey" FOREIGN KEY ("petugasId") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
