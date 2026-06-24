import { Router } from 'express';
import rateLimit from 'express-rate-limit';
import { z } from 'zod';
import { loginController, registerController, meController, changePasswordController } from '../controllers/auth.controller.js';
import { authenticate } from '../middlewares/auth.middleware.js';
import { validate } from '../middlewares/validate.js';

const router = Router();

const LoginSchema = z.object({
    email: z.string().email(),
    password: z.string().min(6),
});

const ChangePasswordSchema = z.object({
    oldPassword: z.string().min(6),
    newPassword: z.string().min(6),
}).strict();

// 10 attempts / 15 min per IP — protects against brute force
const loginLimiter = rateLimit({
    windowMs: 15 * 60 * 1000,
    max: 10,
    standardHeaders: true,
    legacyHeaders: false,
    message: { success: false, message: 'Too many login attempts. Try again in 15 minutes.' },
});

const RegisterSchema = z.object({
    name: z.string().min(2),
    email: z.string().email(),
    password: z.string().min(6),
}).strict();

// POST /auth/register — publik (WARGA)
router.post('/register', loginLimiter, validate({ body: RegisterSchema }), registerController);

// POST /auth/login
router.post('/login', loginLimiter, validate({ body: LoginSchema }), loginController);

// GET /auth/me — current user
router.get('/me', authenticate, meController);

// PUT /auth/password — change own password
router.put('/password', authenticate, validate({ body: ChangePasswordSchema }), changePasswordController);

export default router;
