import { Router } from 'express';
import { authenticate, authorize } from '../middlewares/auth.middleware.js';
import {
    listDeviceStates,
    getStatus,
    postCommand,
    cameraStart,
    cameraStop,
    toggleLogs,
} from '../controllers/devices.controller.js';

const router = Router();

// Perintah di bawah ini menggerakkan perangkat keras di lapangan (kamera,
// aktuator pemilah), jadi dikunci ADMIN — bukan sekadar login.
router.use(authenticate, authorize('ADMIN'));

router.get('/state', listDeviceStates);
router.get('/:nodeId/status', getStatus);          // ?live=1 → tanya langsung ke Pi
router.post('/:nodeId/command', postCommand);      // generic escape hatch
router.post('/:nodeId/camera/start', cameraStart);
router.post('/:nodeId/camera/stop', cameraStop);
router.post('/:nodeId/logs', toggleLogs);          // { on, ttl } → event DEVICE_LOG di WS

export default router;
