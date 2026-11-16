# Runtime image: the app, the corpus, and the embedding + OCR models baked in, so a container
# needs no network except Postgres and the Anthropic API.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    RAGDEMO_MODEL_CACHE=/models \
    HF_HUB_OFFLINE=1

# libgl/libglib: OpenCV inside rapidocr; libgomp: onnxruntime threads
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

# Download the models at build time (the only network access the image ever needs for them).
RUN HF_HUB_OFFLINE=0 uv run python -c "from ragdemo.embed import Embedder; from ragdemo.ingest.ocr import Ocr; from pathlib import Path; Embedder(cache_dir=Path('/models')); Ocr(); print('models cached')"

COPY data/corpus ./data/corpus
COPY data/eval/questions.json data/eval/latest-*.md ./data/eval/

ENV RAGDEMO_CORPUS_DIR=data/corpus \
    RAGDEMO_AUTO_INGEST=true \
    PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"
CMD ["sh", "-c", "uv run --no-sync uvicorn ragdemo.web.app:app --host 0.0.0.0 --port ${PORT}"]
