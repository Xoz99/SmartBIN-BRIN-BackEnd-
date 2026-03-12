import { Router } from 'express';
import { z } from 'zod';
import { getUsersController, getUserByIdController, createUserController, updateUserController, deleteUserController } from '../controllers/users.controller.js';
import { authenticate, authorize } from '../middlewares/auth.middleware.js';
import { validate } from '../middlewares/validate.js';

const router = Router();

// Only ADMIN can manage users
router.use(authenticate, authorize('ADMIN'));

const createUserSchema = z.object({
    name: z.string().min(2),
    email: z.string().email(),
    password: z.string().min(6),
    role: z.enum(['ADMIN', 'PETUGAS']).default('PETUGAS'),
    areaId: z.string().optional().nullable(),
}).strict();

const updateUserSchema = z.object({
    name: z.string().min(2).optional(),
    email: z.string().email().optional(),
    password: z.string().min(6).optional(),
    role: z.enum(['ADMIN', 'PETUGAS']).optional(),
    areaId: z.string().optional().nullable(),
}).strict();

router.get('/', getUsersController);
router.get('/:id', getUserByIdController);
router.post('/', validate({ body: createUserSchema }), createUserController);
router.put('/:id', validate({ body: updateUserSchema }), updateUserController);
router.delete('/:id', deleteUserController);

export default router;
