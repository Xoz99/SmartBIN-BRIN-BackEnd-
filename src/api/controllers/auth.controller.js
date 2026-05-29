import { login, changePassword } from '../../services/auth.service.js';
import { getUserById } from '../../services/user.service.js';
import { success, error } from '../../utils/response.js';

export async function loginController(req, res) {
    try {
        const { token, user } = await login(req.body.email, req.body.password);
        return success(res, { token, user }, 'Login successful');
    } catch (err) {
        return error(res, err.message, err.statusCode || 401);
    }
}

export async function meController(req, res) {
    const user = await getUserById(req.user.id);
    if (!user) return error(res, 'User not found', 404);
    return success(res, user);
}

export async function changePasswordController(req, res) {
    try {
        await changePassword(req.user.id, req.body.oldPassword, req.body.newPassword);
        return success(res, null, 'Password updated');
    } catch (err) {
        return error(res, err.message, err.statusCode || 400);
    }
}
