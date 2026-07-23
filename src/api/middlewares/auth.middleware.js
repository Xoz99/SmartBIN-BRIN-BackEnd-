import { verifyToken } from '../../services/auth.service.js';
import { error } from '../../utils/response.js';
import { env } from '../../config/env.js';
import { logger } from '../../utils/logger.js';

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
 * Device auth middleware — untuk perangkat (raspi) yang push data via HTTP.
 * Bukan JWT: cocokkan header `X-Device-Key` dengan env DEVICE_INGEST_KEY.
 * Kalau DEVICE_INGEST_KEY belum di-set, endpoint dibiarkan terbuka (dev lokal)
 * dengan peringatan di log.
 */
export function deviceAuth(req, res, next) {
    const configured = env.DEVICE_INGEST_KEY;
    if (!configured) {
        logger.warn('[deviceAuth] DEVICE_INGEST_KEY belum di-set — endpoint device terbuka.');
        return next();
    }
    if (req.headers['x-device-key'] !== configured) {
        return error(res, 'Invalid device key', 401);
    }
    next();
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
