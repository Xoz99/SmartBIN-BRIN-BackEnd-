-- CreateEnum
CREATE TYPE "ScheduleStatus" AS ENUM ('PENDING', 'PROSES', 'SELESAI');

-- CreateTable
CREATE TABLE "schedules" (
    "id" TEXT NOT NULL,
    "petugasId" TEXT NOT NULL,
    "areaId" TEXT,
    "date" TIMESTAMP(3) NOT NULL,
    "startTime" TEXT NOT NULL,
    "endTime" TEXT NOT NULL,
    "truck" TEXT,
    "binTarget" INTEGER NOT NULL DEFAULT 0,
    "note" TEXT,
    "status" "ScheduleStatus" NOT NULL DEFAULT 'PENDING',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "schedules_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "schedules_petugasId_idx" ON "schedules"("petugasId");

-- CreateIndex
CREATE INDEX "schedules_date_idx" ON "schedules"("date");

-- AddForeignKey
ALTER TABLE "schedules" ADD CONSTRAINT "schedules_petugasId_fkey" FOREIGN KEY ("petugasId") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "schedules" ADD CONSTRAINT "schedules_areaId_fkey" FOREIGN KEY ("areaId") REFERENCES "areas"("id") ON DELETE SET NULL ON UPDATE CASCADE;
