import { upsertSnapshot, getRecentSnapshots, dayKey } from '../models/snapshot.model.js';
import { sumWeightPerDay } from '../models/deposit.model.js';
import { getAllZona } from './prediksi.service.js';
import { logger } from '../utils/logger.js';

/**
 * Total volume sampah seluruh zona HARI INI (kg) — jumlah actual_kg semua kecamatan.
 */
async function totalZoneKgToday() {
    const zona = await getAllZona();
    return zona.reduce((s, z) => s + (z.actual_kg ?? 0), 0);
}

/**
 * Simpan snapshot untuk HARI INI (dipanggil scheduler / saat startup).
 */
export async function snapshotToday() {
    try {
        const total = await totalZoneKgToday();
        await upsertSnapshot(dayKey(), Math.round(total));
        logger.info(`[Snapshot] Hari ini: ${Math.round(total)} kg tersimpan`);
    } catch (e) {
        logger.warn(`[Snapshot] gagal snapshot hari ini: ${e.message}`);
    }
}

/**
 * Backfill 7 hari ke belakang dari data deposit (sekali, saat startup).
 * Hanya mengisi hari yang BELUM ada snapshot-nya (tidak menimpa data resmi).
 */
export async function backfillSnapshots(days = 7) {
    try {
        const existing = new Map((await getRecentSnapshots(days)).map(s => [s.day, s.totalKg]));
        const perDay = await sumWeightPerDay(days);
        for (let i = days - 1; i >= 0; i--) {
            const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - i);
            const key = dayKey(d);
            if (existing.has(key)) continue;            // sudah ada → jangan timpa
            const kg = Math.round(perDay.get(key) ?? 0);
            await upsertSnapshot(key, kg);
        }
        logger.info('[Snapshot] Backfill 7 hari selesai');
    } catch (e) {
        logger.warn(`[Snapshot] gagal backfill: ${e.message}`);
    }
}

/**
 * Grafik N hari: kembalikan deret {day:"YYYY-MM-DD", totalKg} yang KONTINU
 * (selalu N titik, hari ini di paling kanan). Sumber tiap hari:
 *   - pakai snapshot zona bila ada & > 0 (data resmi dari sensor/zona),
 *   - kalau snapshot 0/absen → fallback ke total berat deposit hari itu.
 * Bikin grafik tetap terisi dari deposit riil walau snapshot zona belum jalan.
 */
export async function getWeeklyVolume(days = 7) {
    const snaps = await getRecentSnapshots(days);
    const snapMap = new Map(snaps.map((s) => [s.day, s.totalKg]));
    const perDay = await sumWeightPerDay(days); // Map "YYYY-MM-DD" -> kg dari deposit

    const out = [];
    for (let i = days - 1; i >= 0; i--) {
        const d = new Date();
        d.setHours(0, 0, 0, 0);
        d.setDate(d.getDate() - i);
        const key = dayKey(d);
        const snapKg = snapMap.get(key) ?? 0;
        const depKg = Math.round(perDay.get(key) ?? 0);
        out.push({ day: key, totalKg: snapKg > 0 ? snapKg : depKg });
    }
    return out;
}

/**
 * Scheduler ringan: cek tiap 1 jam, kalau hari berganti → snapshot.
 * Tidak pakai library cron (hemat RAM). Snapshot hari ini di-refresh juga
 * tiap jam supaya angka hari berjalan ikut update.
 */
let _lastDay = null;
export function startSnapshotScheduler() {
    const tick = async () => {
        const today = dayKey();
        await snapshotToday();        // refresh hari ini
        _lastDay = today;
    };
    // jalankan sekali saat start, lalu tiap 1 jam
    tick();
    setInterval(tick, 60 * 60 * 1000);
    logger.info('[Snapshot] Scheduler aktif (cek tiap 1 jam)');
}
