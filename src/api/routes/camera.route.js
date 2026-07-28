import { Router } from 'express';
import express from 'express';
import { deviceAuth } from '../middlewares/auth.middleware.js';
import { setFrame, getFrame } from '../../services/cameraFrame.service.js';

const router = Router();

// POST /camera/frame — Pi push frame JPEG MENTAH (body = bytes gambar).
// Header: X-Device-Key (deviceAuth) + X-Node-Id. Non-blocking, simpan yang terbaru.
router.post(
    '/frame',
    deviceAuth,
    express.raw({ type: ['image/jpeg', 'application/octet-stream'], limit: '4mb' }),
    (req, res) => {
        const nodeId = req.headers['x-node-id'];
        if (!nodeId) return res.status(400).json({ success: false, message: 'X-Node-Id required' });
        if (!req.body || !req.body.length) return res.status(400).json({ success: false, message: 'empty frame' });
        setFrame(nodeId, req.body);
        return res.json({ success: true });
    }
);

// GET /camera/:nodeId/latest.jpg — frame terakhir buat <img> di dashboard.
// Publik (tanpa auth) supaya bisa dipakai langsung di src <img>. Frame basi → 404.
router.get('/:nodeId/latest.jpg', (req, res) => {
    const f = getFrame(req.params.nodeId);
    if (!f) return res.status(404).send('no frame');
    res.set('Content-Type', 'image/jpeg');
    res.set('Cache-Control', 'no-store');
    // Helmet default CORP=same-origin akan blokir <img> dari origin FE (Vercel).
    // Override → cross-origin biar dashboard bisa nampilin frame.
    res.set('Cross-Origin-Resource-Policy', 'cross-origin');
    return res.send(f.buf);
});

export default router;
