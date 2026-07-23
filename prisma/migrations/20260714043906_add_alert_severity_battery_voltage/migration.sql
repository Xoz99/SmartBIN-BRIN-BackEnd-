-- CreateEnum
CREATE TYPE "AlertSeverity" AS ENUM ('WARNING', 'CRITICAL');

-- AlterTable
ALTER TABLE "alerts" ADD COLUMN     "severity" "AlertSeverity" NOT NULL DEFAULT 'WARNING';

-- AlterTable
ALTER TABLE "sensor_logs" ADD COLUMN     "batteryVoltage" DOUBLE PRECISION;

-- CreateIndex
CREATE INDEX "alerts_severity_idx" ON "alerts"("severity");
