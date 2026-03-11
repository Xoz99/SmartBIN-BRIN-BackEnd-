import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import { findUserByEmail, findUserById } from '../models/user.model.js';
import { env } from '../config/env.js';

/**
 * Login — verify credentials and return JWT
 * @param {string} email
 * @param {string} password
 * @returns {{ token: string, user: object }}
 */
export async function login(email, password) {
    const user = await findUserByEmail(email);
    if (!user) throw Object.assign(new Error('Invalid credentials'), { statusCode: 401 });

    const valid = await bcrypt.compare(password, user.password);
    if (!valid) throw Object.assign(new Error('Invalid credentials'), { statusCode: 401 });

    const payload = { id: user.id, email: user.email, role: user.role, areaId: user.areaId };
    const token = jwt.sign(payload, env.JWT_SECRET, { expiresIn: env.JWT_EXPIRES_IN });

    return {
        token,
        user: {
            id: user.id,
            name: user.name,
            email: user.email,
            role: user.role,
            areaId: user.areaId,
        },
    };
}

/**
 * Verify JWT and return decoded payload
 * @param {string} token
 * @returns {{ id: string, email: string, role: string }}
 */
export function verifyToken(token) {
    return jwt.verify(token, env.JWT_SECRET);
}

/**
 * Hash a plain text password
 * @param {string} password
 */
export async function hashPassword(password) {
    return bcrypt.hash(password, 12);
}
