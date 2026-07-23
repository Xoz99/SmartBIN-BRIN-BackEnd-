import { Router } from 'express';
import { sensorIngestController } from '../controllers/ingest.controller.js';
import { deviceAuth } from '../middlewares/auth.middleware.js';

const router = Router();

// POST /ingest/sensor — Raspi LoRa-RX push data sensor via HTTP (auth: X-Device-Key)
router.post('/sensor', deviceAuth, sensorIngestController);

export default router;
