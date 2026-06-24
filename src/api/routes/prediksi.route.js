import { Router } from 'express';
import {
  listTpsController,
  getPrediksiController,
  getSummaryController,
} from '../controllers/prediksi.controller.js';

const router = Router();

// Public — data peta TPS bisa diakses tanpa login
router.get('/summary', getSummaryController);
router.get('/tps', listTpsController);
router.get('/tps/:tpsId', getPrediksiController);

export default router;
