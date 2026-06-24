-- AlterEnum
ALTER TYPE "UserRole" ADD VALUE 'WARGA';

-- AlterTable
ALTER TABLE "sensor_logs" ADD COLUMN     "distance" DOUBLE PRECISION;

-- CreateTable
CREATE TABLE "deposits" (
    "id" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "binId" TEXT NOT NULL,
    "label" "WasteLabel" NOT NULL,
    "confidence" DOUBLE PRECISION,
    "weight" DOUBLE PRECISION,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "deposits_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "disposals" (
    "id" TEXT NOT NULL,
    "kecamatan" TEXT NOT NULL,
    "weightKg" DOUBLE PRECISION NOT NULL,
    "confirmedBy" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "disposals_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "zone_snapshots" (
    "id" TEXT NOT NULL,
    "day" TEXT NOT NULL,
    "totalKg" DOUBLE PRECISION NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "zone_snapshots_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "deposits_userId_idx" ON "deposits"("userId");

-- CreateIndex
CREATE INDEX "deposits_binId_idx" ON "deposits"("binId");

-- CreateIndex
CREATE INDEX "deposits_createdAt_idx" ON "deposits"("createdAt");

-- CreateIndex
CREATE INDEX "disposals_kecamatan_idx" ON "disposals"("kecamatan");

-- CreateIndex
CREATE INDEX "disposals_createdAt_idx" ON "disposals"("createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "zone_snapshots_day_key" ON "zone_snapshots"("day");

-- AddForeignKey
ALTER TABLE "deposits" ADD CONSTRAINT "deposits_userId_fkey" FOREIGN KEY ("userId") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "deposits" ADD CONSTRAINT "deposits_binId_fkey" FOREIGN KEY ("binId") REFERENCES "bins"("id") ON DELETE CASCADE ON UPDATE CASCADE;
