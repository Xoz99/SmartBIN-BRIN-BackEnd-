import { transmissionStats } from '../../mqtt/transmissionStats.js';
import { success, error } from '../../utils/response.js';

/**
 * GET /monitoring/transmission
 * Byterate/throughput transmisi MQTT semua node + agregat.
 */
export function getTransmissionStats(_req, res) {
    return success(res, transmissionStats.getAll(), 'Transmission stats');
}

/**
 * GET /monitoring/transmission/:nodeId
 * Byterate/throughput transmisi MQTT satu node.
 */
export function getNodeTransmissionStats(req, res) {
    const stats = transmissionStats.getByNode(req.params.nodeId);
    if (!stats) {
        return error(res, `No transmission data for node '${req.params.nodeId}'`, 404);
    }
    return success(res, stats, 'Transmission stats');
}
