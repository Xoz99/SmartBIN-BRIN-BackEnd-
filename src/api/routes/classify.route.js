import { Router } from 'express';
import multer from 'multer';
import { classifyController } from '../controllers/classify.controller.js';
import { authenticate } from '../middlewares/auth.middleware.js';

const router = Router();

// Use memory storage so we keep the buffer in-memory
const upload = multer({
    storage: multer.memoryStorage(),
    limits: { fileSize: 10 * 1024 * 1024 }, // 10MB max
    fileFilter: (_req, file, cb) => {
        if (!file.mimetype.startsWith('image/')) {
            return cb(new Error('Only image files are allowed'), false);
        }
        cb(null, true);
    },
});

// POST /classify
router.post(
    '/',
    authenticate,
    upload.single('image'),
    classifyController
);

export default router;
