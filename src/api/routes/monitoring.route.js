import { Router } from 'express';
import {
    getTransmissionStats,
    getNodeTransmissionStats,
} from '../controllers/monitoring.controller.js';

const router = Router();

// Tanpa auth — dipakai CLI monitoring internal di core server (konsisten dgn /health).

// GET /monitoring/transmission
router.get('/transmission', getTransmissionStats);

// GET /monitoring/transmission/:nodeId
router.get('/transmission/:nodeId', getNodeTransmissionStats);

export default router;
