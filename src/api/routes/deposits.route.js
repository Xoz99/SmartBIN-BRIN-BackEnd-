import { Router } from 'express';
import { z } from 'zod';
import { createDepositController, listDepositsController } from '../controllers/deposits.controller.js';
import { authenticate } from '../middlewares/auth.middleware.js';
import { validate } from '../middlewares/validate.js';

const router = Router();

const CreateDepositSchema = z.object({
    nodeId: z.string().min(1),
    label: z.enum(['organik', 'anorganik', 'b3', 'unknown']),
    confidence: z.number().min(0).max(1).optional(),
    weight: z.number().min(0).max(200).optional(),
}).strict();

// POST /deposits — semua user terautentikasi (WARGA termasuk)
router.post('/', authenticate, validate({ body: CreateDepositSchema }), createDepositController);

// GET /deposits — histori
router.get('/', authenticate, listDepositsController);

export default router;
