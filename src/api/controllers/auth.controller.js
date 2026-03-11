import { login } from '../../services/auth.service.js';
import { success, error } from '../../utils/response.js';

export async function loginController(req, res) {
    try {
        const { token, user } = await login(req.body.email, req.body.password);
        return success(res, { token, user }, 'Login successful');
    } catch (err) {
        return error(res, err.message, err.statusCode || 401);
    }
}
