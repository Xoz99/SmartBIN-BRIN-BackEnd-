import { Router } from 'express';
import { z } from 'zod';
import { createAreaController, getAreasController, getAreaByIdController, updateAreaController, deleteAreaController } from '../controllers/areas.controller.js';
import { authenticate, authorize } from '../middlewares/auth.middleware.js';
import { validate } from '../middlewares/validate.js';

const router = Router();

// Only ADMIN can manage areas
router.use(authenticate, authorize('ADMIN'));

const createAreaSchema = z.object({
    name: z.string().min(3).max(100),
});

const updateAreaSchema = z.object({
    name: z.string().min(3).max(100),
});

router.post('/', validate({ body: createAreaSchema }), createAreaController);
router.get('/', getAreasController);
router.get('/:id', getAreaByIdController);
router.put('/:id', validate({ body: updateAreaSchema }), updateAreaController);
router.delete('/:id', deleteAreaController);

export default router;
