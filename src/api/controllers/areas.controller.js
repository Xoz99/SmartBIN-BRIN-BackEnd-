import { createArea, getAllAreas, getAreaById, updateArea, deleteArea } from '../../services/area.service.js';
import { success, error } from '../../utils/response.js';

export async function createAreaController(req, res) {
    const area = await createArea(req.body);
    return success(res, area, 'Area created', 201);
}

export async function getAreasController(req, res) {
    const areas = await getAllAreas();
    return success(res, areas, 'Areas retrieved');
}

export async function getAreaByIdController(req, res) {
    const area = await getAreaById(req.params.id);
    if (!area) return error(res, 'Area not found', 404);
    return success(res, area);
}

export async function updateAreaController(req, res) {
    try {
        const area = await updateArea(req.params.id, req.body);
        return success(res, area, 'Area updated');
    } catch (err) {
        if (err.code === 'P2025') return error(res, 'Area not found', 404);
        throw err;
    }
}

export async function deleteAreaController(req, res) {
    try {
        await deleteArea(req.params.id);
        return success(res, null, 'Area deleted');
    } catch (err) {
        if (err.code === 'P2025') return error(res, 'Area not found', 404);
        throw err;
    }
}
