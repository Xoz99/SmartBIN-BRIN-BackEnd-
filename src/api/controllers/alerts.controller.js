import { getAlerts, resolveAlert, getAlertWithBin } from '../../services/alert.service.js';
import { success, error, paginated } from '../../utils/response.js';

/**
 * GET /alerts?resolved=false&page=1&limit=50
 */
export async function listAlerts(req, res) {
    const resolved = req.query.resolved !== undefined
        ? req.query.resolved === 'true'
        : undefined;

    const limit = parseInt(req.query.limit) || 50;
    const page = parseInt(req.query.page) || 1;

    const { items, total } = await getAlerts(req.user, { resolved }, limit, page);
    return paginated(res, items, total, page, limit, 'Alerts retrieved');
}

/**
 * PUT /alerts/:id/resolve
 */
export async function resolveAlertController(req, res) {
    try {
        // Area ownership check — PETUGAS can only resolve alerts in their area
        if (req.user.role === 'PETUGAS' && req.user.areaId) {
            const alertData = await getAlertWithBin(req.params.id);
            if (!alertData) return error(res, 'Alert not found', 404);
            if (alertData.bin?.areaId && alertData.bin.areaId !== req.user.areaId) {
                return error(res, 'Forbidden: alert is not in your area', 403);
            }
        }

        const alert = await resolveAlert(req.params.id);
        return success(res, alert, 'Alert resolved');
    } catch (err) {
        if (err.code === 'P2025') return error(res, 'Alert not found', 404);
        throw err;
    }
}
