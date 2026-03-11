import { classifyImage } from '../../services/classify.service.js';
import { success, error } from '../../utils/response.js';

/**
 * POST /classify
 * Accepts: multipart/form-data with field 'image' (file)
 *       OR JSON body with { image: base64string }
 */
export async function classifyController(req, res) {
    try {
        let result;

        // Multipart file upload (via multer)
        if (req.file) {
            result = await classifyImage(req.file.buffer, 'buffer');
        } else if (req.body.image) {
            result = await classifyImage(req.body.image, 'base64');
        } else {
            return error(res, 'No image provided. Send form-data with "image" file OR JSON with "image" base64 string', 400);
        }

        return success(res, result, 'Classification complete');
    } catch (err) {
        if (err.statusCode === 503) {
            return error(res, 'Classification service unavailable', 503);
        }
        throw err;
    }
}
