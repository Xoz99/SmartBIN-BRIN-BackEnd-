import {
    createSchedule,
    listSchedules,
    getScheduleById,
    updateSchedule,
    deleteSchedule,
} from '../../services/schedule.service.js';
import { success, error, paginated } from '../../utils/response.js';

/**
 * POST /schedules — admin membuat jadwal untuk petugas
 */
export async function createScheduleController(req, res) {
    const { schedule, error: err } = await createSchedule(req.body);
    if (err) return error(res, err.message, err.status);
    return success(res, schedule, 'Jadwal dibuat', 201);
}

/**
 * GET /schedules?petugasId=&status=&date=&page=&limit=
 */
export async function listSchedulesController(req, res) {
    let { petugasId } = req.query;
    const { status, date } = req.query;
    const limit = parseInt(req.query.limit) || 100;
    const page = parseInt(req.query.page) || 1;

    // Petugas hanya boleh melihat jadwalnya sendiri
    if (req.user.role === 'PETUGAS') petugasId = req.user.id;

    const { items, total } = await listSchedules({ petugasId, status, date }, limit, page);
    return paginated(res, items, total, page, limit, 'Schedules retrieved');
}

/**
 * GET /schedules/:id
 */
export async function getScheduleController(req, res) {
    const schedule = await getScheduleById(req.params.id);
    if (!schedule) return error(res, 'Schedule not found', 404);
    return success(res, schedule);
}

/**
 * PUT /schedules/:id — admin update jadwal (termasuk ubah status)
 */
export async function updateScheduleController(req, res) {
    const { schedule, error: err } = await updateSchedule(req.params.id, req.body);
    if (err) return error(res, err.message, err.status);
    return success(res, schedule, 'Jadwal diperbarui');
}

/**
 * DELETE /schedules/:id
 */
export async function deleteScheduleController(req, res) {
    const { error: err } = await deleteSchedule(req.params.id);
    if (err) return error(res, err.message, err.status);
    return success(res, null, 'Jadwal dihapus');
}
