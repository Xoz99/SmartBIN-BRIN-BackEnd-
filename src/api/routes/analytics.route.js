import { Router } from 'express';
import { getWeeklyClassifications } from '../../services/classification.service.js';
import { authenticate } from '../middlewares/auth.middleware.js';
import { success, error } from '../../utils/response.js';

const router = Router();

// GET /analytics/weekly-volume — jumlah sampah terpilah per hari (7 hari terakhir).
// Pakai count klasifikasi (reliable dari kamera), bukan berat (load cell belum
// tentu di-tare). Balikin [{day, count}].
router.get('/weekly-volume', authenticate, async (_req, res) => {
    try {
        const data = await getWeeklyClassifications(7);
        return success(res, data, 'Sampah terpilah 7 hari');
    } catch (err) {
        return error(res, err.message, 500);
    }
});

export default router;
