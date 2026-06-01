import { Router } from 'express';
import { z } from 'zod';
import {
    createScheduleController,
    listSchedulesController,
    getScheduleController,
    updateScheduleController,
    deleteScheduleController,
} from '../controllers/schedules.controller.js';
import { authenticate, authorize } from '../middlewares/auth.middleware.js';
import { validate } from '../middlewares/validate.js';

const router = Router();

const CreateScheduleSchema = z.object({
    petugasId: z.string().min(1),
    areaId: z.string().min(1).optional().nullable(),
    date: z.string().min(1), // ISO date, mis. "2026-06-01"
    startTime: z.string().min(1),
    endTime: z.string().min(1),
    truck: z.string().optional().nullable(),
    binTarget: z.number().int().min(0).optional(),
    note: z.string().optional().nullable(),
}).strict();

const UpdateScheduleSchema = z.object({
    petugasId: z.string().min(1).optional(),
    areaId: z.string().min(1).optional().nullable(),
    date: z.string().min(1).optional(),
    startTime: z.string().min(1).optional(),
    endTime: z.string().min(1).optional(),
    truck: z.string().optional().nullable(),
    binTarget: z.number().int().min(0).optional(),
    note: z.string().optional().nullable(),
    status: z.enum(['PENDING', 'PROSES', 'SELESAI']).optional(),
}).strict();

// GET /schedules — admin: semua; petugas: jadwalnya sendiri
router.get('/', authenticate, listSchedulesController);

// GET /schedules/:id
router.get('/:id', authenticate, getScheduleController);

// POST /schedules — admin only
router.post(
    '/',
    authenticate,
    authorize('ADMIN'),
    validate({ body: CreateScheduleSchema }),
    createScheduleController,
);

// PUT /schedules/:id — admin only
router.put(
    '/:id',
    authenticate,
    authorize('ADMIN'),
    validate({ body: UpdateScheduleSchema }),
    updateScheduleController,
);

// DELETE /schedules/:id — admin only
router.delete('/:id', authenticate, authorize('ADMIN'), deleteScheduleController);

export default router;
