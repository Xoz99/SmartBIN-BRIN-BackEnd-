import { Router } from 'express';
import { z } from 'zod';
import { getZonesController, createDisposalController } from '../controllers/disposals.controller.js';
import { authenticate, authorize } from '../middlewares/auth.middleware.js';
import { validate } from '../middlewares/validate.js';

const router = Router();

const CreateDisposalSchema = z.object({
    kecamatan: z.string().min(1),
    weightKg: z.number().positive().max(100000),
}).strict();

// GET /disposals/zones — rekap per kecamatan (ADMIN/PETUGAS)
router.get('/zones', authenticate, authorize('ADMIN', 'PETUGAS'), getZonesController);

// POST /disposals — konfirmasi pemusnahan (ADMIN/PETUGAS)
router.post('/', authenticate, authorize('ADMIN', 'PETUGAS'), validate({ body: CreateDisposalSchema }), createDisposalController);

export default router;
