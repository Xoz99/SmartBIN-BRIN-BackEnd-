import * as model from '../models/schedule.model.js';
import { findUserById } from '../models/user.model.js';
import { broadcast } from '../websocket/ws.js';
import { logger } from '../utils/logger.js';

/**
 * Admin membuat jadwal untuk seorang petugas.
 * Area di-snapshot dari area petugas jika tidak diberikan.
 */
export async function createSchedule(input) {
    const petugas = await findUserById(input.petugasId);
    if (!petugas) return { error: { message: 'Petugas not found', status: 404 } };
    if (petugas.role !== 'PETUGAS') return { error: { message: 'User bukan petugas', status: 400 } };

    const schedule = await model.createSchedule({
        petugasId: input.petugasId,
        areaId: input.areaId ?? petugas.areaId ?? null,
        date: new Date(input.date),
        startTime: input.startTime,
        endTime: input.endTime,
        truck: input.truck ?? null,
        binTarget: input.binTarget ?? 0,
        note: input.note ?? null,
    });

    logger.info(`[ScheduleService] 🗓️ Jadwal dibuat untuk ${petugas.email} (${schedule.id})`);
    broadcast('SCHEDULE_CREATED', {
        scheduleId: schedule.id,
        petugasId: schedule.petugasId,
        areaId: schedule.areaId,
        date: schedule.date,
        status: schedule.status,
    });

    return { schedule };
}

export async function listSchedules(filters, limit, page) {
    return model.findAllSchedules(filters, limit, page);
}

export async function getScheduleById(id) {
    return model.findScheduleById(id);
}

export async function updateSchedule(id, input) {
    const existing = await model.findScheduleById(id);
    if (!existing) return { error: { message: 'Schedule not found', status: 404 } };

    const data = {};
    if (input.petugasId !== undefined) data.petugasId = input.petugasId;
    if (input.areaId !== undefined) data.areaId = input.areaId;
    if (input.date !== undefined) data.date = new Date(input.date);
    if (input.startTime !== undefined) data.startTime = input.startTime;
    if (input.endTime !== undefined) data.endTime = input.endTime;
    if (input.truck !== undefined) data.truck = input.truck;
    if (input.binTarget !== undefined) data.binTarget = input.binTarget;
    if (input.note !== undefined) data.note = input.note;
    if (input.status !== undefined) data.status = input.status;

    const schedule = await model.updateSchedule(id, data);
    broadcast('SCHEDULE_UPDATED', {
        scheduleId: schedule.id,
        petugasId: schedule.petugasId,
        status: schedule.status,
    });
    return { schedule };
}

export async function deleteSchedule(id) {
    const existing = await model.findScheduleById(id);
    if (!existing) return { error: { message: 'Schedule not found', status: 404 } };
    await model.deleteSchedule(id);
    broadcast('SCHEDULE_DELETED', { scheduleId: id });
    return { ok: true };
}
