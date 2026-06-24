import { recordDeposit, listDeposits } from '../../services/deposit.service.js';
import { success, error } from '../../utils/response.js';

// POST /deposits — WARGA/PETUGAS/ADMIN: catat setoran + kirim perintah pilah
export async function createDepositController(req, res) {
    try {
        const { nodeId, label, confidence, weight } = req.body;
        const data = await recordDeposit({ userId: req.user.id, nodeId, label, confidence, weight });
        return success(res, data, 'Setoran tercatat & perintah dikirim', 201);
    } catch (err) {
        return error(res, err.message, err.statusCode || 400);
    }
}

// GET /deposits?binId=&userId=&nodeId= — histori/tracking
export async function listDepositsController(req, res) {
    const { binId, userId, nodeId } = req.query;
    const data = await listDeposits({ binId, userId, nodeId });
    return success(res, data, 'Histori deposit');
}
