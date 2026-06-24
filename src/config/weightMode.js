import { redisClient } from './redis.js';
import { logger } from '../utils/logger.js';

// =====================================================================
// MODE PEMBACAAN BERAT TONG  (ubah lewat .env WEIGHT_MODE, default di sini)
// =====================================================================
//  'loadcell'        -> berat MURNI dari sensor load cell; saat sensor kirim
//                       berat, langsung ditempel ke deposit terakhir TANPA
//                       menunggu konfirmasi. [VISI AKHIR / saat load cell ada]
//
//  'user'            -> berat dari input manual user di /ecosort (sistem lama).
//                       [HAPUS saat full-sensor]
//
//  'sensor_pairing'  -> [AKTIF SEKARANG] URUTAN FISIK: JENIS DIDETEKSI DULU,
//                       BERAT MENYUSUL. Kamera klasifikasi jenis sampah ->
//                       label ditahan sebagai "pending label" (TTL window) di
//                       Redis -> saat load cell kirim BERAT via MQTT sensor ->
//                       label pending + berat dipasangkan -> deposit OTOMATIS
//                       dibuat + jadi berat tong terkonfirmasi. Kalau berat
//                       telat (> window) -> pending label hangus.
// =====================================================================
export const WEIGHT_MODE = process.env.WEIGHT_MODE || 'sensor_pairing';

// Lama window konfirmasi (detik). TTL Redis = auto-hangus.
export const PAIRING_TTL_SEC = parseInt(process.env.WEIGHT_PAIRING_TTL, 10) || 10;

const pendingKey      = (binId) => `bin:${binId}:pendingWeight`;  // berat menunggu konfirmasi (mode lama)
const pendingLabelKey = (binId) => `bin:${binId}:pendingLabel`;   // jenis sampah menunggu berat (sensor_pairing)
const confirmedKey    = (binId) => `bin:${binId}:weight`;         // berat tong terkonfirmasi terakhir

// ── Pending weight (sensor kirim, belum dikonfirmasi user) ──────────────
export async function setPendingWeight(binId, weight) {
    if (!redisClient) return;
    try {
        await redisClient.set(pendingKey(binId), String(weight), 'EX', PAIRING_TTL_SEC);
    } catch (e) {
        logger.warn(`[weightMode] gagal set pendingWeight: ${e.message}`);
    }
}

export async function getPendingWeight(binId) {
    if (!redisClient) return null;
    try {
        const v = await redisClient.get(pendingKey(binId));
        return v == null ? null : parseFloat(v);
    } catch (e) {
        logger.warn(`[weightMode] gagal get pendingWeight: ${e.message}`);
        return null;
    }
}

export async function clearPendingWeight(binId) {
    if (!redisClient) return;
    try { await redisClient.del(pendingKey(binId)); } catch { /* ignore */ }
}

// ── Pending label (JENIS sampah terdeteksi kamera, menunggu berat) ──────
// sensor_pairing: kamera klasifikasi DULU → simpan {label, confidence, userId}
// → menunggu berat dari load cell (window = PAIRING_TTL_SEC) → di-commit jadi
// deposit oleh handler sensor begitu berat tiba.
export async function setPendingLabel(binId, { label, confidence, userId }) {
    if (!redisClient) return;
    try {
        await redisClient.set(
            pendingLabelKey(binId),
            JSON.stringify({ label, confidence: confidence ?? null, userId: userId ?? null }),
            'EX', PAIRING_TTL_SEC,
        );
    } catch (e) {
        logger.warn(`[weightMode] gagal set pendingLabel: ${e.message}`);
    }
}

export async function getPendingLabel(binId) {
    if (!redisClient) return null;
    try {
        const v = await redisClient.get(pendingLabelKey(binId));
        return v == null ? null : JSON.parse(v);
    } catch (e) {
        logger.warn(`[weightMode] gagal get pendingLabel: ${e.message}`);
        return null;
    }
}

export async function clearPendingLabel(binId) {
    if (!redisClient) return;
    try { await redisClient.del(pendingLabelKey(binId)); } catch { /* ignore */ }
}

// ── Confirmed weight (berat tong resmi, dipakai monitoring) ─────────────
export async function setConfirmedWeight(binId, weight) {
    if (!redisClient) return;
    try { await redisClient.set(confirmedKey(binId), String(weight)); } catch { /* ignore */ }
}

export async function getConfirmedWeight(binId) {
    if (!redisClient) return 0;
    try {
        const v = await redisClient.get(confirmedKey(binId));
        return v == null ? 0 : parseFloat(v);
    } catch { return 0; }
}
