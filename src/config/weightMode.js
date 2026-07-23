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
//  'sensor_pairing'  -> URUTAN FISIK: JENIS DIDETEKSI DULU, BERAT MENYUSUL.
//                       Kamera klasifikasi jenis sampah -> label ditahan
//                       sebagai "pending label" (TTL window) di Redis -> saat
//                       load cell kirim BERAT via MQTT sensor -> label pending +
//                       berat dipasangkan -> deposit OTOMATIS dibuat + jadi berat
//                       tong terkonfirmasi. Kalau berat telat (> window) ->
//                       pending label hangus.
//
//  'accumulate'      -> [AKTIF SEKARANG] BERAT TONG = TOTAL BERJALAN. Tiap
//                       penimbangan (berat naik lalu balik ~0 = 1 episode)
//                       dijumlahkan ke total, pakai berat PUNCAK episode supaya
//                       tidak double-count walau sensor publish tiap 5 detik.
//                       Total naik terus selama sampah masuk, reset 0 saat tong
//                       diangkut (pickup). Kalau kamera sempat klasifikasi jenis
//                       pada episode itu -> deposit ikut dibuat.
// =====================================================================
export const WEIGHT_MODE = process.env.WEIGHT_MODE || 'sensor_pairing';

// Lama window konfirmasi (detik). TTL Redis = auto-hangus.
export const PAIRING_TTL_SEC = parseInt(process.env.WEIGHT_PAIRING_TTL, 10) || 10;

// Ambang "timbangan kosong" (kg). Bacaan <= nilai ini dianggap 0 (noise load
// cell tanpa tare). Dipakai mode 'accumulate' untuk mendeteksi awal/akhir episode.
export const ZERO_THRESHOLD_KG = parseFloat(process.env.WEIGHT_ZERO_THRESHOLD) || 0.05;

const pendingKey      = (binId) => `bin:${binId}:pendingWeight`;  // berat menunggu konfirmasi (mode lama)
const pendingLabelKey = (binId) => `bin:${binId}:pendingLabel`;   // jenis sampah menunggu berat (sensor_pairing)
const confirmedKey    = (binId) => `bin:${binId}:weight`;         // berat tong terkonfirmasi / total berjalan
const weighPeakKey    = (binId) => `bin:${binId}:weighPeak`;      // berat puncak episode timbang berjalan (accumulate)

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

// ── Akumulasi berat (mode 'accumulate') ─────────────────────────────────
// Bersihkan sisa episode timbang berjalan (dipanggil saat tong diangkut).
export async function clearWeighEpisode(binId) {
    if (!redisClient) return;
    try { await redisClient.del(weighPeakKey(binId)); } catch { /* ignore */ }
}

/**
 * Catat satu bacaan load cell dan deteksi apakah 1 penimbangan sudah SELESAI.
 *
 * Load cell reset ke ~0 antar item, jadi tiap barang = satu episode: berat
 * NAIK di atas ambang lalu BALIK ~0. Selama berat di atas ambang, kita simpan
 * nilai PUNCAK episode. Begitu berat balik ~0, puncak itu = berat item yang
 * baru ditimbang → dijumlahkan sekali ke total (INCRBYFLOAT). Karena hanya
 * di-commit saat balik ke 0, sensor yang publish tiap 5 detik tidak double-count.
 *
 * @param {string} binId
 * @param {number} rawWeight berat live dari load cell (kg)
 * @returns {Promise<{committed:boolean, added?:number, total?:number}>}
 *   committed=true berarti 1 penimbangan baru saja masuk ke total.
 */
export async function recordWeighing(binId, rawWeight) {
    if (!redisClient) return { committed: false };
    const w = Number(rawWeight) || 0;
    try {
        if (w > ZERO_THRESHOLD_KG) {
            // Masih ada barang di timbangan → update puncak episode ini.
            const prevPeak = parseFloat(await redisClient.get(weighPeakKey(binId))) || 0;
            if (w > prevPeak) await redisClient.set(weighPeakKey(binId), String(w));
            return { committed: false };
        }
        // Timbangan balik ~0 → kalau ada puncak, episode selesai = 1 penimbangan.
        const peak = parseFloat(await redisClient.get(weighPeakKey(binId))) || 0;
        if (peak <= ZERO_THRESHOLD_KG) return { committed: false };
        await redisClient.del(weighPeakKey(binId));
        const total = parseFloat(await redisClient.incrbyfloat(confirmedKey(binId), peak));
        return { committed: true, added: peak, total };
    } catch (e) {
        logger.warn(`[weightMode] gagal recordWeighing: ${e.message}`);
        return { committed: false };
    }
}
