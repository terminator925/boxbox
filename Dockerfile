FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
ENV VITE_BOXBOX_BUILD_STAMP=render
RUN npm run build


FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV BOXBOX_INFER_ACCELERATOR=torch
ENV BOXBOX_INFER_CANDIDATE_STRATEGY=core4_adaptive_plus
ENV BOXBOX_HYBRID_SEARCH_STRATEGY=core4

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg rubberband-cli libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /tmp/requirements.txt

COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY models/ /app/models/
COPY README.md /app/README.md

COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

RUN mkdir -p /app/uploads /app/outputs

EXPOSE 10000

CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-10000}"]
