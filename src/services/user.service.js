import { prisma } from '../config/db.js';
import { hashPassword } from './auth.service.js';

export async function getAllUsers() {
    return prisma.user.findMany({
        select: {
            id: true,
            name: true,
            email: true,
            role: true,
            areaId: true,
            area: { select: { id: true, name: true } },
            createdAt: true,
        },
        orderBy: { createdAt: 'desc' },
    });
}

export async function getUserById(id) {
    return prisma.user.findUnique({
        where: { id },
        select: {
            id: true,
            name: true,
            email: true,
            role: true,
            areaId: true,
            area: { select: { id: true, name: true } },
            createdAt: true,
        },
    });
}

export async function createUserWithArea(data) {
    const hashedPassword = await hashPassword(data.password);
    return prisma.user.create({
        data: {
            ...data,
            password: hashedPassword,
        },
        select: { id: true, name: true, email: true, role: true, areaId: true },
    });
}

export async function updateUser(id, data) {
    const updateData = { ...data };
    if (updateData.password) {
        updateData.password = await hashPassword(updateData.password);
    }
    return prisma.user.update({
        where: { id },
        data: updateData,
        select: { id: true, name: true, email: true, role: true, areaId: true },
    });
}

export async function deleteUser(id) {
    return prisma.user.delete({ where: { id } });
}
