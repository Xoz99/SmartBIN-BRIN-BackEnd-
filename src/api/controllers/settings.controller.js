import { getSettings, setPickupThreshold } from '../../services/settings.service.js';
import { success, error } from '../../utils/response.js';

// GET /settings — setelan operasional global (mis. pickupThreshold)
export async function getSettingsController(_req, res) {
    try {
        const data = await getSettings();
        return success(res, data, 'Setelan sistem');
    } catch (err) {
        return error(res, err.message, err.statusCode || 500);
    }
}

// PUT /settings/pickup-threshold — ubah ambang "Perlu Diangkut" (ADMIN/PETUGAS)
export async function setPickupThresholdController(req, res) {
    try {
        const value = await setPickupThreshold(req.body.value);
        return success(res, { pickupThreshold: value }, 'Ambang pengangkutan diperbarui');
    } catch (err) {
        return error(res, err.message, err.statusCode || 400);
    }
}
