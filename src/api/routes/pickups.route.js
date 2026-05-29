import { Router } from 'express';
import { z } from 'zod';
import { completePickupController, listPickupsController, getPickupController } from '../controllers/pickups.controller.js';
import { authenticate, authorize } from '../middlewares/auth.middleware.js';
import { validate } from '../middlewares/validate.js';

const router = Router();

const CompletePickupSchema = z.object({
    lat: z.number().optional(),
    lng: z.number().optional(),
}).strict();

// POST /pickups/:binId/complete — petugas (area-nya) + admin
router.post(
    '/:binId/complete',
    authenticate,
    authorize('ADMIN', 'PETUGAS'),
    validate({ body: CompletePickupSchema }),
    completePickupController,
);

// GET /pickups — list riwayat (petugas hanya areanya)
router.get('/', authenticate, listPickupsController);

// GET /pickups/:id — detail
router.get('/:id', authenticate, getPickupController);

export default router;
