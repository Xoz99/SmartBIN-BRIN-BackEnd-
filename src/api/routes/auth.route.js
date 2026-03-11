import { Router } from 'express';
import { z } from 'zod';
import { loginController } from '../controllers/auth.controller.js';
import { validate } from '../middlewares/validate.js';

const router = Router();

const LoginSchema = z.object({
    email: z.string().email(),
    password: z.string().min(6),
});

// POST /auth/login
router.post('/login', validate({ body: LoginSchema }), loginController);

export default router;
