import { createDisposal } from '../models/disposal.model.js';
import { getAllZona } from './prediksi.service.js';
import { broadcast } from '../websocket/ws.js';
import { logger } from '../utils/logger.js';

/**
 * Rekap volume sampah per zona/kecamatan untuk panel "Manajemen TPA".
 * getAllZona() sudah berisi: fill_pct, alert, total_tps, terkumpul_kg,
 * musnah_resmi_kg (total dimusnahkan), sisa_tpa_kg. Pemusnahan langsung
 * mengurangi sisa TPA (tanpa masa tunggu).
 */
export async function getZoneWaste() {
    const zona = await getAllZona();
    // urutkan dari sisa TPA terbanyak (paling butuh dimusnahkan)
    return zona
        .map(z => ({
            kecamatan: z.kecamatan,
            fill_pct: z.fill_pct,
            alert: z.alert,
            total_tps: z.total_tps,
            terkumpul_kg: z.terkumpul_kg ?? 0,
            proses_musnah_kg: z.proses_musnah_kg ?? 0,
            musnah_resmi_kg: z.musnah_resmi_kg ?? 0,
            sisa_tpa_kg: z.sisa_tpa_kg ?? 0,
        }))
        .sort((a, b) => b.sisa_tpa_kg - a.sisa_tpa_kg);
}

/**
 * Catat pemusnahan (parsial) untuk sebuah kecamatan.
 * @param {{kecamatan:string, weightKg:number, userId?:string}} input
 */
export async function recordDisposal({ kecamatan, weightKg, userId }) {
    if (!kecamatan) throw Object.assign(new Error('Kecamatan wajib diisi'), { statusCode: 400 });
    if (!(weightKg > 0)) throw Object.assign(new Error('Berat pemusnahan harus > 0'), { statusCode: 400 });

    const row = await createDisposal({ kecamatan, weightKg, confirmedBy: userId });
    logger.info(`[Disposal] ${kecamatan}: ${weightKg}kg dikonfirmasi musnah oleh ${userId ?? '-'}`);

    try {
        broadcast('DISPOSAL_RECORDED', { kecamatan: row.kecamatan, weightKg: row.weightKg, at: row.createdAt });
    } catch { /* best-effort */ }

    return row;
}
