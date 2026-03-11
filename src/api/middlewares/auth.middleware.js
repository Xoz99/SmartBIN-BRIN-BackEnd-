import { verifyToken } from '../../services/auth.service.js';
import { error } from '../../utils/response.js';

/**
 * JWT auth middleware — reads token from Authorization: Bearer <token>
 * Attaches decoded user to req.user
 */
export function authenticate(req, res, next) {
    const header = req.headers.authorization;
    if (!header || !header.startsWith('Bearer ')) {
        return error(res, 'Authorization token required', 401);
    }

    const token = header.slice(7);
    try {
        req.user = verifyToken(token);
        next();
    } catch {
        return error(res, 'Invalid or expired token', 401);
    }
}

/**
 * Role-based access control middleware
 * Usage: authorize('ADMIN') or authorize('ADMIN', 'PETUGAS')
 * @param {...string} roles
 */
export function authorize(...roles) {
    return (req, res, next) => {
        if (!req.user) return error(res, 'Unauthorized', 401);
        if (!roles.includes(req.user.role)) {
            return error(res, 'Forbidden: insufficient permissions', 403);
        }
        next();
    };
}
