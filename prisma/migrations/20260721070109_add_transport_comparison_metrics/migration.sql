-- AlterTable
ALTER TABLE "sensor_logs" ADD COLUMN     "latencyMs" INTEGER,
ADD COLUMN     "sentAt" TIMESTAMP(3),
ADD COLUMN     "seq" INTEGER,
ADD COLUMN     "transport" TEXT;
