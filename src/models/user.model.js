import { prisma } from '../config/db.js';

/**
 * Find a user by email address
 * @param {string} email
 */
export async function findUserByEmail(email) {
    return prisma.user.findUnique({ where: { email } });
}

/**
 * Find a user by primary key
 * @param {string} id
 */
export async function findUserById(id) {
    return prisma.user.findUnique({
        where: { id },
        select: { id: true, name: true, email: true, role: true, deviceToken: true, createdAt: true },
    });
}

/**
 * Create a new user
 * @param {{ name, email, password, role }} data
 */
export async function createUser(data) {
    return prisma.user.create({
        data,
        select: { id: true, name: true, email: true, role: true, createdAt: true },
    });
}

/**
 * Get all users with PETUGAS role, optionally filtered by area (for FCM notifications)
 * @param {string} [areaId]
 */
export async function findAllPetugas(areaId) {
    const where = { role: 'PETUGAS', deviceToken: { not: null } };
    if (areaId) {
        where.areaId = areaId;
    }

    return prisma.user.findMany({
        where,
        select: { id: true, name: true, deviceToken: true },
    });
}

/**
 * Update FCM device token
 * @param {string} id
 * @param {string} token
 */
export async function updateDeviceToken(id, token) {
    return prisma.user.update({ where: { id }, data: { deviceToken: token } });
}
