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
// Anti brute-force login. Diatur lewat env:
//   AUTH_RATE_LIMIT_MAX         = maks percobaan per window (default 10; 0 = MATIKAN total)
//   AUTH_RATE_LIMIT_WINDOW_MIN  = menit window (default 15)
const RATE_MAX = Number(process.env.AUTH_RATE_LIMIT_MAX ?? 10);
const RATE_WINDOW_MIN = Number(process.env.AUTH_RATE_LIMIT_WINDOW_MIN ?? 15);
const loginLimiter = RATE_MAX > 0
    ? rateLimit({
        windowMs: RATE_WINDOW_MIN * 60 * 1000,
        max: RATE_MAX,
        standardHeaders: true,
        legacyHeaders: false,
        message: { success: false, message: `Too many login attempts. Try again in ${RATE_WINDOW_MIN} minutes.` },
    })
    : (_req, _res, next) => next(); // AUTH_RATE_LIMIT_MAX=0 → limiter dimatikan (dev)

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
