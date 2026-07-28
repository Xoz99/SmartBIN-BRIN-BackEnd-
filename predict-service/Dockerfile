# syntax=docker/dockerfile:1
FROM python:3.11-slim
WORKDIR /app

# libGL/glib buat Pillow di beberapa format; ai-edge-litert = LiteRT runtime (x86_64).
RUN pip install --no-cache-dir \
    fastapi "uvicorn[standard]" python-multipart pillow numpy ai-edge-litert

COPY main.py .

EXPOSE 9000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
