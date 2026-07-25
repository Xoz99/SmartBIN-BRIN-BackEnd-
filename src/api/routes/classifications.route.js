import { Router } from 'express';
import {
    classificationSummaryController,
    classificationListController,
    classificationIngestController,
} from '../controllers/classifications.controller.js';
import { authenticate, deviceAuth } from '../middlewares/auth.middleware.js';

const router = Router();

// POST /classifications — device (raspi) push hasil pemilahan via HTTP (auth: X-Device-Key)
router.post('/', deviceAuth, classificationIngestController);

// GET /classifications/summary — agregasi jenis sampah (untuk Analitik FE)
router.get('/summary', authenticate, classificationSummaryController);

// GET /classifications — daftar deteksi terbaru (panel "Jenis Sampah Terdeteksi")
router.get('/', authenticate, classificationListController);

export default router;
