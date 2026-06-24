import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import { findUserByEmail, findUserById, createUser } from '../models/user.model.js';
import { prisma } from '../config/db.js';
import { env } from '../config/env.js';

function signToken(user) {
    const payload = { id: user.id, email: user.email, role: user.role, areaId: user.areaId ?? null };
    return jwt.sign(payload, env.JWT_SECRET, { expiresIn: env.JWT_EXPIRES_IN });
}

/**
 * Register publik — buat user WARGA baru, langsung kasih token
 * @param {{name:string, email:string, password:string}} data
 */
export async function register({ name, email, password }) {
    const existing = await findUserByEmail(email);
    if (existing) throw Object.assign(new Error('Email sudah terdaftar'), { statusCode: 409 });

    const hashed = await hashPassword(password);
    const user = await createUser({ name, email, password: hashed, role: 'WARGA' });
    return { token: signToken(user), user };
}

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

/**
 * Change a user's password — verifies old, hashes new
 * @param {string} userId
 * @param {string} oldPassword
 * @param {string} newPassword
 */
export async function changePassword(userId, oldPassword, newPassword) {
    const user = await prisma.user.findUnique({ where: { id: userId } });
    if (!user) throw Object.assign(new Error('User not found'), { statusCode: 404 });

    const valid = await bcrypt.compare(oldPassword, user.password);
    if (!valid) throw Object.assign(new Error('Old password is incorrect'), { statusCode: 400 });

    if (oldPassword === newPassword) {
        throw Object.assign(new Error('New password must be different'), { statusCode: 400 });
    }

    const hashed = await hashPassword(newPassword);
    await prisma.user.update({ where: { id: userId }, data: { password: hashed } });
}
