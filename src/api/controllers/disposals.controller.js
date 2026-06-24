import { getZoneWaste, recordDisposal } from '../../services/disposal.service.js';
import { success, error } from '../../utils/response.js';

// GET /disposals/zones — rekap volume sampah per kecamatan + rincian TPA
export async function getZonesController(_req, res) {
    try {
        const data = await getZoneWaste();
        return success(res, data, 'Rekap volume sampah per zona');
    } catch (err) {
        return error(res, err.message, err.statusCode || 500);
    }
}

// POST /disposals — konfirmasi pemusnahan (parsial) untuk satu kecamatan
export async function createDisposalController(req, res) {
    try {
        const { kecamatan, weightKg } = req.body;
        const data = await recordDisposal({ kecamatan, weightKg, userId: req.user.id });
        return success(res, data, 'Pemusnahan dikonfirmasi', 201);
    } catch (err) {
        return error(res, err.message, err.statusCode || 400);
    }
}
