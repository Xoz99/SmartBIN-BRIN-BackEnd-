import { Router } from 'express';
import { getWeeklyVolume } from '../../services/snapshot.service.js';
import { authenticate } from '../middlewares/auth.middleware.js';
import { success, error } from '../../utils/response.js';

const router = Router();

// GET /analytics/weekly-volume — total volume zona per hari (7 hari terakhir)
router.get('/weekly-volume', authenticate, async (_req, res) => {
    try {
        const data = await getWeeklyVolume(7);
        return success(res, data, 'Volume zona 7 hari');
    } catch (err) {
        return error(res, err.message, 500);
    }
});

export default router;
