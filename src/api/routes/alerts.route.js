import { Router } from 'express';
import { listAlerts, resolveAlertController } from '../controllers/alerts.controller.js';
import { authenticate } from '../middlewares/auth.middleware.js';

const router = Router();

// GET /alerts
router.get('/', authenticate, listAlerts);

// PUT /alerts/:id/resolve
router.put('/:id/resolve', authenticate, resolveAlertController);

export default router;
