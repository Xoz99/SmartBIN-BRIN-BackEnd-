import { prisma } from '../config/db.js';

export async function createArea(data) {
    return prisma.area.create({ data });
}

export async function getAllAreas() {
    return prisma.area.findMany({
        orderBy: { createdAt: 'asc' },
        include: {
            _count: {
                select: { bins: true, users: true },
            },
        },
    });
}

export async function getAreaById(id) {
    return prisma.area.findUnique({
        where: { id },
        include: {
            bins: { select: { id: true, nodeId: true, location: true } },
            users: { select: { id: true, name: true, email: true, role: true } },
        },
    });
}

export async function updateArea(id, data) {
    return prisma.area.update({ where: { id }, data });
}

export async function deleteArea(id) {
    return prisma.area.delete({ where: { id } });
}
