# Footnote API image. CPU-only PyTorch keeps the image a fraction of the CUDA size.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf FOOTNOTE_DATA_DIR=/app/data
WORKDIR /app

RUN pip install --index-url https://download.pytorch.org/whl/cpu torch

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# Bake the embedding model into the image so start-up needs no network.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"
ENV HF_HUB_OFFLINE=1

COPY migrations ./migrations
COPY data/snapshot ./data/snapshot
COPY eval ./eval
COPY scripts/docker-entrypoint.sh /usr/local/bin/entrypoint
RUN chmod +x /usr/local/bin/entrypoint && useradd --create-home footnote && chown -R footnote /app
USER footnote

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
ENTRYPOINT ["entrypoint"]
CMD ["uvicorn", "footnote.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
