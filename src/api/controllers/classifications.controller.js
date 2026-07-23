import { getClassificationSummary } from '../../services/classification.service.js';
import { handleClassificationData } from '../../mqtt/handlers/classificationData.js';
import { success, error } from '../../utils/response.js';

// GET /classifications/summary?from=&to=&binId=&areaId=
export async function classificationSummaryController(req, res) {
    try {
        const { from, to, binId } = req.query;
        // PETUGAS otomatis dibatasi ke area-nya; ADMIN bebas (boleh filter via query).
        const areaId =
            req.user.role === 'PETUGAS' && req.user.areaId
                ? req.user.areaId
                : req.query.areaId || undefined;

        const data = await getClassificationSummary({ from, to, binId, areaId });
        return success(res, data, 'Ringkasan klasifikasi');
    } catch (err) {
        return error(res, err.message, 500);
    }
}

// POST /classifications  body: { nodeId, label, confidence }
// Jalur HTTP untuk device (raspi) yang push hasil pemilahan — alternatif MQTT.
// Memakai ulang handleClassificationData supaya perilakunya identik dengan jalur MQTT.
export async function classificationIngestController(req, res) {
    try {
        const { nodeId, label } = req.body || {};
        if (!nodeId) return error(res, 'nodeId is required', 400);

        const confidence = Number(req.body?.confidence);
        const record = await handleClassificationData(nodeId, {
            label,
            confidence: Number.isFinite(confidence) ? confidence : 0,
        });
        if (!record) return error(res, `Unknown nodeId: ${nodeId}`, 404);

        return success(
            res,
            {
                id: record.id,
                label: record.label,
                confidence: record.confidence,
                createdAt: record.createdAt,
            },
            'Classification recorded',
            201
        );
    } catch (err) {
        return error(res, err.message, 500);
    }
}
