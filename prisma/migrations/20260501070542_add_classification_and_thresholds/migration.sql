-- CreateEnum
CREATE TYPE "AlertType" AS ENUM ('FULL_WEIGHT', 'FULL_VOLUME', 'BATTERY_LOW', 'GAS_HIGH');

-- CreateEnum
CREATE TYPE "WasteLabel" AS ENUM ('organik', 'anorganik', 'b3', 'unknown');

-- CreateEnum
CREATE TYPE "UserRole" AS ENUM ('ADMIN', 'PETUGAS');

-- CreateTable
CREATE TABLE "bins" (
    "id" TEXT NOT NULL,
    "nodeId" TEXT NOT NULL,
    "location" TEXT NOT NULL,
    "lat" DOUBLE PRECISION NOT NULL,
    "lng" DOUBLE PRECISION NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "weightThreshold" DOUBLE PRECISION,
    "volumeThreshold" DOUBLE PRECISION,
    "gasThreshold" DOUBLE PRECISION,
    "batteryThreshold" DOUBLE PRECISION,
    "areaId" TEXT,

    CONSTRAINT "bins_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "sensor_logs" (
    "id" TEXT NOT NULL,
    "binId" TEXT NOT NULL,
    "weight" DOUBLE PRECISION NOT NULL,
    "volume" DOUBLE PRECISION NOT NULL,
    "battery" DOUBLE PRECISION NOT NULL,
    "gas" DOUBLE PRECISION,
    "rssi" INTEGER NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "sensor_logs_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "alerts" (
    "id" TEXT NOT NULL,
    "binId" TEXT NOT NULL,
    "type" "AlertType" NOT NULL,
    "message" TEXT NOT NULL,
    "resolved" BOOLEAN NOT NULL DEFAULT false,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "resolvedAt" TIMESTAMP(3),

    CONSTRAINT "alerts_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "classifications" (
    "id" TEXT NOT NULL,
    "binId" TEXT NOT NULL,
    "label" "WasteLabel" NOT NULL,
    "confidence" DOUBLE PRECISION NOT NULL,
    "rawResult" JSONB,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "classifications_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "users" (
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "password" TEXT NOT NULL,
    "role" "UserRole" NOT NULL DEFAULT 'PETUGAS',
    "deviceToken" TEXT,
    "areaId" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "areas" (
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "areas_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "bins_nodeId_key" ON "bins"("nodeId");

-- CreateIndex
CREATE INDEX "sensor_logs_binId_idx" ON "sensor_logs"("binId");

-- CreateIndex
CREATE INDEX "sensor_logs_createdAt_idx" ON "sensor_logs"("createdAt");

-- CreateIndex
CREATE INDEX "alerts_binId_idx" ON "alerts"("binId");

-- CreateIndex
CREATE INDEX "alerts_resolved_idx" ON "alerts"("resolved");

-- CreateIndex
CREATE INDEX "classifications_binId_idx" ON "classifications"("binId");

-- CreateIndex
CREATE INDEX "classifications_createdAt_idx" ON "classifications"("createdAt");

-- CreateIndex
CREATE UNIQUE INDEX "users_email_key" ON "users"("email");

-- CreateIndex
CREATE UNIQUE INDEX "areas_name_key" ON "areas"("name");

-- AddForeignKey
ALTER TABLE "bins" ADD CONSTRAINT "bins_areaId_fkey" FOREIGN KEY ("areaId") REFERENCES "areas"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "sensor_logs" ADD CONSTRAINT "sensor_logs_binId_fkey" FOREIGN KEY ("binId") REFERENCES "bins"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "alerts" ADD CONSTRAINT "alerts_binId_fkey" FOREIGN KEY ("binId") REFERENCES "bins"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "classifications" ADD CONSTRAINT "classifications_binId_fkey" FOREIGN KEY ("binId") REFERENCES "bins"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "users" ADD CONSTRAINT "users_areaId_fkey" FOREIGN KEY ("areaId") REFERENCES "areas"("id") ON DELETE SET NULL ON UPDATE CASCADE;
