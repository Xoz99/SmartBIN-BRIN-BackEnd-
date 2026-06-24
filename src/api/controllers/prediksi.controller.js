import { getAllZona, getSummary, getPrediksiKecamatan } from '../../services/prediksi.service.js';
import { logger } from '../../utils/logger.js';

// GET /prediksi/tps
export async function listTpsController(req, res, next) {
  try {
    const data = await getAllZona();
    res.json({ success: true, data });
  } catch (err) {
    logger.error('[Prediksi] listTps:', err.message);
    next(err);
  }
}

// GET /prediksi/tps/:kecamatan — prediksi volume per kecamatan
export async function getPrediksiController(req, res, next) {
  try {
    const kecamatan = decodeURIComponent(req.params.tpsId);
    const data = await getPrediksiKecamatan(kecamatan);
    if (!data) {
      return res.status(404).json({ success: false, error: 'Kecamatan tidak ditemukan' });
    }
    res.json({ success: true, data });
  } catch (err) {
    logger.error('[Prediksi] getPrediksi:', err.message);
    next(err);
  }
}

// GET /prediksi/summary
export async function getSummaryController(req, res, next) {
  try {
    const data = await getSummary();
    res.json({ success: true, data });
  } catch (err) {
    logger.error('[Prediksi] getSummary:', err.message);
    next(err);
  }
}
