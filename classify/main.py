"""
SmartBin — YOLOv5 Classification Microservice
FastAPI + torch (YOLOv5 nano)

Endpoints:
  POST /classify  — multipart file OR JSON { image: base64 }
"""

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import torch
import base64
import io
import os
from pathlib import Path
from PIL import Image
import uvicorn

# ─── Config ──────────────────────────────────────────────────────────────────
MODEL_PATH = Path(__file__).parent / "model" / "best.pt"
CONFIDENCE_THRESHOLD = 0.5
CLASSES = ["organik", "anorganik", "b3"]

app = FastAPI(
    title="SmartBin Classify Service",
    description="YOLOv5 nano waste classification microservice",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Load Model ───────────────────────────────────────────────────────────────
model = None

@app.on_event("startup")
async def load_model():
    global model
    if not MODEL_PATH.exists():
        print(f"[WARN] Model not found at {MODEL_PATH}. Service will return 503 for classify requests.")
        return
    try:
        model = torch.hub.load(
            "ultralytics/yolov5",
            "custom",
            path=str(MODEL_PATH),
            force_reload=False,
            verbose=False,
        )
        model.conf = CONFIDENCE_THRESHOLD
        model.classes = list(range(len(CLASSES)))
        print(f"[INFO] YOLOv5 model loaded from {MODEL_PATH}")
    except Exception as e:
        print(f"[ERROR] Failed to load model: {e}")
        model = None


# ─── Helper ───────────────────────────────────────────────────────────────────
def run_inference(image: Image.Image) -> dict:
    """Run YOLOv5 inference and return structured result."""
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded. Place best.pt in classify/model/")

    results = model(image)
    detections = results.pandas().xyxy[0]  # DataFrame

    all_detections = []
    for _, row in detections.iterrows():
        class_idx = int(row["class"])
        label = CLASSES[class_idx] if class_idx < len(CLASSES) else f"class_{class_idx}"
        all_detections.append({
            "label": label,
            "confidence": round(float(row["confidence"]), 4),
            "bbox": {
                "x1": round(float(row["xmin"]), 2),
                "y1": round(float(row["ymin"]), 2),
                "x2": round(float(row["xmax"]), 2),
                "y2": round(float(row["ymax"]), 2),
            },
        })

    # Sort by confidence descending
    all_detections.sort(key=lambda x: x["confidence"], reverse=True)

    if not all_detections:
        return {"label": "unknown", "confidence": 0.0, "all_detections": []}

    best = all_detections[0]
    return {
        "label": best["label"],
        "confidence": best["confidence"],
        "all_detections": all_detections,
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

class Base64Request(BaseModel):
    image: str  # base64 encoded image string


@app.get("/health")
async def health():
    return {"status": "ok", "model_loaded": model is not None}


@app.post("/classify")
async def classify(
    file: UploadFile | None = File(default=None),
    body: Base64Request | None = None,
):
    """
    Classify waste image.
    - Accepts multipart form-data with 'file' field
    - OR JSON body with 'image' (base64 string)
    """
    image: Image.Image | None = None

    # Mode 1: file upload
    if file is not None:
        contents = await file.read()
        try:
            image = Image.open(io.BytesIO(contents)).convert("RGB")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid image file")

    # Mode 2: base64 JSON
    elif body is not None:
        try:
            image_bytes = base64.b64decode(body.image)
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 image string")

    else:
        raise HTTPException(status_code=400, detail="Provide 'file' (multipart) or 'image' (base64 JSON)")

    return run_inference(image)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
