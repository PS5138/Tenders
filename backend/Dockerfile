# One image for the API and the worker. Build context is the repository root
# (see docker-compose.yml and /.dockerignore) so pyproject.toml, backend/ and eval/ are all
# available. The image is CPU-only: no GPU is declared in compose and Docling's layout and
# TableFormer weights run on the CPU.
#
# The base is pinned to the trixie (Debian 13) variant so the apt package names below are stable.
FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DOCLING_ARTIFACTS_PATH=/opt/docling-models

# docling -> rapidocr -> opencv-python, whose Linux wheel links libGL.so.1 and
# libgthread-2.0.so.0. Without them every PDF ingest fails at run time with an ImportError from
# docling_ibm_models' TableFormer predictor (the one unguarded `import cv2`); docx and xlsx are
# unaffected, which is why the build alone does not catch it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0t64 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so source changes do not invalidate the layer.
# --torch-backend cpu makes uv take torch and torchvision (pulled in by docling-ibm-models)
# from the CPU wheel index instead of PyPI's CUDA build, which would add several gigabytes of
# nvidia-* libraries the container can never use. Every other package still comes from PyPI.
COPY pyproject.toml /app/pyproject.toml
RUN pip install --upgrade pip uv \
 && uv pip install --system --torch-backend cpu -r /app/pyproject.toml

# Docling's PDF layout and TableFormer weights are the one local model in the build.
# They are downloaded here, at image build time, into DOCLING_ARTIFACTS_PATH; the PDF
# pipeline is constructed with that path and do_ocr=False so nothing is fetched at run time.
# (CLI verified against docling 2.130: `docling-tools models download [MODELS]... -o DIR`.)
RUN docling-tools models download layout tableformer -o "$DOCLING_ARTIFACTS_PATH"

COPY backend/ /app/backend/
COPY eval/ /app/eval/

# Run as an unprivileged user. /data/storage must exist and be owned by that user in the
# image so the named `storage` volume inherits the ownership when compose first creates it
# (an existing volume from an earlier root image needs `docker compose down -v`, or a one-off
# `chown 10001 /data/storage`, otherwise uploads fail to create their directories). The code
# under /app stays root-owned and read-only; PYTHONDONTWRITEBYTECODE means nothing is written
# there. HOME points at /tmp so any library cache has somewhere writable.
RUN useradd -r -u 10001 -d /tmp -s /usr/sbin/nologin app \
 && mkdir -p /data/storage \
 && chown app:app /data/storage
ENV HOME=/tmp
USER app

WORKDIR /app/backend
EXPOSE 8000

# Default command; compose overrides it per service.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
