import { completePickup, manualConfirmPickup, getPickups, getPickupById } from '../../services/pickup.service.js';
import { success, error, paginated } from '../../utils/response.js';

/**
 * POST /pickups/:binId/complete
 * Petugas menekan "Selesai" — catat checkpoint (siapa/kapan/GPS).
 */
export async function completePickupController(req, res) {
    const { lat, lng } = req.body ?? {};
    const { pickup, error: err } = await completePickup(req.params.binId, req.user, { lat, lng });

    if (err) return error(res, err.message, err.status);
    return success(res, pickup, 'Pickup dicatat, menunggu konfirmasi sensor', 201);
}

/**
 * POST /pickups/:id/confirm
 * Petugas/Admin konfirmasi manual — fallback saat sensor error.
 */
export async function manualConfirmPickupController(req, res) {
    const { pickup, error: err } = await manualConfirmPickup(req.params.id, req.user);

    if (err) return error(res, err.message, err.status);
    return success(res, pickup, 'Pickup dikonfirmasi manual (selesai)');
}

/**
 * GET /pickups?status=&binId=&page=&limit=
 */
export async function listPickupsController(req, res) {
    const { status, binId } = req.query;
    const limit = parseInt(req.query.limit) || 50;
    const page = parseInt(req.query.page) || 1;

    const { items, total } = await getPickups(req.user, { status, binId }, limit, page);
    return paginated(res, items, total, page, limit, 'Pickups retrieved');
}

/**
 * GET /pickups/:id
 */
export async function getPickupController(req, res) {
    const pickup = await getPickupById(req.params.id);
    if (!pickup) return error(res, 'Pickup not found', 404);

    // Area ownership — PETUGAS hanya melihat pickup di areanya
    if (req.user.role === 'PETUGAS' && req.user.areaId && pickup.areaId && pickup.areaId !== req.user.areaId) {
        return error(res, 'Forbidden: pickup is not in your area', 403);
    }

    return success(res, pickup);
}
