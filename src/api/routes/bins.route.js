import { Router } from 'express';
import { z } from 'zod';
import { listBins, getBin, getBinHistoryController, setThresholdController, createBinController, updateBinController, deleteBinController } from '../controllers/bins.controller.js';
import { authenticate, authorize } from '../middlewares/auth.middleware.js';
import { validate } from '../middlewares/validate.js';

const router = Router();

const ThresholdSchema = z.object({
    weightThreshold: z.number().positive().optional(),
    volumeThreshold: z.number().min(1).max(100).optional(),
}).refine((d) => d.weightThreshold || d.volumeThreshold, {
    message: 'At least one threshold must be provided',
});

const CreateBinSchema = z.object({
    nodeId: z.string().min(1),
    location: z.string().min(3),
    lat: z.number(),
    lng: z.number(),
    areaId: z.string().optional().nullable(),
});

const UpdateBinSchema = z.object({
    nodeId: z.string().min(1).optional(),
    location: z.string().min(3).optional(),
    lat: z.number().optional(),
    lng: z.number().optional(),
    areaId: z.string().optional().nullable(),
});

// GET /bins
router.get('/', authenticate, listBins);

// GET /bins/:id
router.get('/:id', authenticate, getBin);

// GET /bins/:id/history
router.get('/:id/history', authenticate, getBinHistoryController);

// POST /bins — ADMIN only
router.post('/', authenticate, authorize('ADMIN'), validate({ body: CreateBinSchema }), createBinController);

// PUT /bins/:id — ADMIN only
router.put('/:id', authenticate, authorize('ADMIN'), validate({ body: UpdateBinSchema }), updateBinController);

// PUT /bins/:id/threshold — ADMIN only
router.put('/:id/threshold', authenticate, authorize('ADMIN'), validate({ body: ThresholdSchema }), setThresholdController);

// DELETE /bins/:id — ADMIN only
router.delete('/:id', authenticate, authorize('ADMIN'), deleteBinController);

export default router;
