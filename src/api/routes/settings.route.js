import { Router } from 'express';
import { z } from 'zod';
import { getSettingsController, setPickupThresholdController } from '../controllers/settings.controller.js';
import { authenticate, authorize } from '../middlewares/auth.middleware.js';
import { validate } from '../middlewares/validate.js';

const router = Router();

const ThresholdSchema = z.object({
    value: z.number().min(50).max(95),
}).strict();

// GET /settings — boleh diakses semua user terautentikasi (dipakai /pickups)
router.get('/', authenticate, getSettingsController);

// PUT /settings/pickup-threshold — ADMIN/PETUGAS
router.put('/pickup-threshold', authenticate, authorize('ADMIN', 'PETUGAS'), validate({ body: ThresholdSchema }), setPickupThresholdController);

export default router;
