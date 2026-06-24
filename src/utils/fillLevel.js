/**
 * Konversi jarak sensor laser VL53L0X (cm) menjadi tingkat penuh (fill %).
 *
 * VL53L0X dipasang di TUTUP tong, mengarah ke bawah. Makin dekat sampah ke
 * sensor (jarak kecil) = makin penuh. Mengikuti ambang yang dipakai di firmware:
 *   > 68.75 cm  -> KOSONG        (0%)
 *   <= 37.0 cm  -> PENUH         (100%)
 * Di antara keduanya dipetakan linear.
 *
 * Catatan: nilai EMPTY/FULL ini bisa berbeda per tong (tinggi tong beda).
 * Untuk sekarang dipakai default global; nanti bisa per-bin di DB.
 */
export const DIST_EMPTY_CM = 68.75; // jarak saat tong kosong (sampah jauh dari sensor)
export const DIST_FULL_CM  = 37.0;  // jarak saat tong penuh (sampah dekat sensor)

/**
 * @param {number} distanceCm  jarak terukur (cm)
 * @returns {number|null} fill percentage 0-100, atau null kalau input tidak valid
 */
export function distanceToFillPct(distanceCm) {
    if (distanceCm == null || Number.isNaN(distanceCm)) return null;

    // Lebih dekat dari batas penuh => anggap 100%
    if (distanceCm <= DIST_FULL_CM) return 100;
    // Lebih jauh dari batas kosong => anggap 0%
    if (distanceCm >= DIST_EMPTY_CM) return 0;

    const span = DIST_EMPTY_CM - DIST_FULL_CM;       // rentang jarak kosong->penuh
    const pct = ((DIST_EMPTY_CM - distanceCm) / span) * 100;
    return Math.round(Math.min(100, Math.max(0, pct)));
}

/**
 * Label status mengikuti firmware (untuk tampilan/log).
 * @param {number} distanceCm
 * @returns {'KOSONG'|'SEDANG'|'HAMPIR PENUH'|'PENUH'|null}
 */
export function distanceToLabel(distanceCm) {
    if (distanceCm == null || Number.isNaN(distanceCm)) return null;
    if (distanceCm > 68.75) return 'KOSONG';
    if (distanceCm > 52.5)  return 'SEDANG';
    if (distanceCm > 37.0)  return 'HAMPIR PENUH';
    return 'PENUH';
}
