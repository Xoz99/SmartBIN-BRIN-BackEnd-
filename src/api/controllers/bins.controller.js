import { getAllBins, getBinById, getBinHistory, setThreshold, getOptimalRoute } from '../../services/bin.service.js';
import { findBinById, createBin as createBinModel, updateBin as updateBinModel, deleteBin as deleteBinModel } from '../../models/bin.model.js';
import { success, error, paginated } from '../../utils/response.js';
import { captureTare, clearTare } from '../../config/weightMode.js';

/**
 * GET /bins
 */
export async function listBins(req, res) {
    const bins = await getAllBins(req.user);
    return success(res, bins, 'Bins retrieved');
}

/**
 * GET /bins/route/optimal?lat=x&lng=y
 */
export async function getOptimalRouteController(req, res) {
    const { lat, lng } = req.query;
    if (!lat || !lng) {
        return error(res, 'Titik koordinat awal (lat, lng) wajib disertakan untuk kalkulasi rute', 400);
    }
    
    const originLat = parseFloat(lat);
    const originLng = parseFloat(lng);

    if (isNaN(originLat) || isNaN(originLng)) {
        return error(res, 'Format koordinat lat/lng tidak valid', 400);
    }

    const result = await getOptimalRoute(originLat, originLng, req.user);
    return success(res, result, 'Rute optimal berhasil di-generate');
}

/**
 * GET /bins/:id
 */
export async function getBin(req, res) {
    const bin = await getBinById(req.params.id);
    if (!bin) return error(res, 'Bin not found', 404);

    // Area ownership check — PETUGAS can only access bins in their area
    if (req.user.role === 'PETUGAS' && req.user.areaId && bin.areaId && bin.areaId !== req.user.areaId) {
        return error(res, 'Forbidden: bin is not in your area', 403);
    }

    return success(res, bin);
}

/**
 * GET /bins/:id/history?limit=50&page=1&from=ISO&to=ISO&transport=lora|http
 * from/to: filter rentang tanggal (createdAt). transport: saring jalur (lora/http).
 */
export async function getBinHistoryController(req, res) {
    const { id } = req.params;
    const { from, to, transport } = req.query;

    // Kalau ada rentang tanggal, default limit dinaikkan (biar seluruh bacaan di
    // periode itu keambil — LoRa tak terpotong window "N terakhir"). Tetap dibatasi.
    const hasRange = Boolean(from || to);
    const limit = Math.min(parseInt(req.query.limit) || (hasRange ? 1000 : 50), 5000);
    const page = parseInt(req.query.page) || 1;

    const bin = await findBinById(id);
    if (!bin) return error(res, 'Bin not found', 404);

    // Area ownership check — PETUGAS can only access bins in their area
    if (req.user.role === 'PETUGAS' && req.user.areaId && bin.areaId && bin.areaId !== req.user.areaId) {
        return error(res, 'Forbidden: bin is not in your area', 403);
    }

    const { items, total } = await getBinHistory(id, limit, page, { from, to, transport });
    return paginated(res, items, total, page, limit, 'History retrieved');
}

/**
 * POST /bins/:id/tare — jadikan bacaan berat terbaru sebagai titik nol (tong KOSONG).
 * Semua berat berikutnya dikurangi offset ini (software tare, tanpa reflash firmware).
 */
export async function tareBinController(req, res) {
    const { id } = req.params;
    const bin = await findBinById(id);
    if (!bin) return error(res, 'Bin not found', 404);

    const r = await captureTare(id);
    if (!r.ok) {
        return error(res, r.reason === 'no-reading'
            ? 'Belum ada bacaan berat terbaru — pastikan alat kirim data & tong kosong, lalu ulangi'
            : 'Tare gagal (cache tidak aktif)', 400);
    }
    return success(res, { nodeId: bin.nodeId, offset: r.offset },
        `Tare tersimpan — ${r.offset} kg dijadikan titik nol`, 200);
}

/**
 * DELETE /bins/:id/tare — hapus offset tare (balik ke bacaan mentah).
 */
export async function clearTareController(req, res) {
    const bin = await findBinById(req.params.id);
    if (!bin) return error(res, 'Bin not found', 404);
    await clearTare(req.params.id);
    return success(res, { nodeId: bin.nodeId }, 'Tare direset (offset dihapus)', 200);
}

/**
 * PUT /bins/:id/threshold
 */
export async function setThresholdController(req, res) {
    const bin = await findBinById(req.params.id);
    if (!bin) return error(res, 'Bin not found', 404);

    const updated = await setThreshold(bin.nodeId, req.body);
    return success(res, updated, 'Threshold updated');
}

/**
 * POST /bins
 */
export async function createBinController(req, res) {
    try {
        const bin = await createBinModel(req.body);
        return success(res, bin, 'Bin created', 201);
    } catch (err) {
        if (err.code === 'P2002') return error(res, 'Bin with this nodeId already exists', 409);
        throw err;
    }
}

/**
 * PUT /bins/:id
 */
export async function updateBinController(req, res) {
    try {
        const bin = await updateBinModel(req.params.id, req.body);
        return success(res, bin, 'Bin updated');
    } catch (err) {
        if (err.code === 'P2025') return error(res, 'Bin not found', 404);
        if (err.code === 'P2002') return error(res, 'Bin with this nodeId already exists', 409);
        throw err;
    }
}

/**
 * DELETE /bins/:id
 */
export async function deleteBinController(req, res) {
    try {
        await deleteBinModel(req.params.id);
        return success(res, null, 'Bin deleted');
    } catch (err) {
        if (err.code === 'P2025') return error(res, 'Bin not found', 404);
        throw err;
    }
}
