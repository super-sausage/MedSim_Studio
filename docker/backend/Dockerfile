# ============================================================
# CT Simulator - Backend Dockerfile
# ============================================================
# Multi-stage build: Python deps → runtime
# ============================================================

# ---- Stage 1: Dependencies ----
FROM python:3.11-slim AS builder

WORKDIR /app

ARG APT_MIRROR
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST=pypi.org
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System dependencies for medical imaging libraries
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    set -eux; \
    if [ -n "${APT_MIRROR:-}" ]; then \
        sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources && \
        sed -i "s|http://security.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    else \
        sed -i "s|http://deb.debian.org|https://deb.debian.org|g" /etc/apt/sources.list.d/debian.sources && \
        sed -i "s|http://security.debian.org|https://security.debian.org|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\nAcquire::ForceIPv4 "true";\n' > /etc/apt/apt.conf.d/80-medisim-retries; \
    for attempt in 1 2 3; do \
        apt-get update && \
        apt-get install -y --no-install-recommends --fix-missing \
            build-essential \
            libglvnd0 \
            libglx0 \
            libglib2.0-0t64 \
            libsm6 \
            libxext6 \
            libxrender1 \
            libgomp1 \
            wget && \
        break; \
        echo "APT install attempt ${attempt} failed; retrying..." >&2; \
        rm -rf /var/lib/apt/lists/*; \
        sleep 5; \
    done; \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies with BuildKit pip cache and local wheel mount.
# Mount ./downloads/ at build time so pip finds the pre-downloaded CUDA
# PyTorch wheel without hitting the slow public CDN.
#
# To pre-download the wheel locally (fast in China):
#   mkdir -p downloads && cd downloads
#   curl -LO https://download.pytorch.org/whl/cu121/torch-2.1.2%2Bcu121-cp311-cp311-linux_x86_64.whl
COPY backend/requirements.runtime.txt ./requirements.txt

# Step 1: Install PyTorch — prefers the local wheel over remote download.
# Strategy: set index-url to Tsinghua mirror (does NOT host CUDA PyTorch
# wheels), so pip won't find torch there and will fall back to --find-links
# to discover the pre-downloaded local wheel.  torch's dependencies
# (filelock, sympy, networkx, …) ARE on PyPI and get fetched from Tsinghua.
#
# The original --extra-index-url=https://download.pytorch.org/whl/cu121
# was removed because it gave pip a direct remote path to the full 2.2 GB
# torch wheel, defeating the purpose of the local file.
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=./downloads,target=/downloads \
    pip install \
    --find-links=/downloads \
    ${PIP_INDEX_URL:+--index-url ${PIP_INDEX_URL}} \
    ${PIP_TRUSTED_HOST:+--trusted-host ${PIP_TRUSTED_HOST}} \
    --timeout 600 \
    torch==2.1.2+cu121

# Step 2: Install rest of Python dependencies from Tsinghua mirror.
# Use a constraint file to PIN torch to the CUDA 12.1 version installed
# in Step 1.  Without this, monai / TotalSegmentator / nnunetv2 pull in
# the latest torch (2.12.0+cu130) whose nvidia-*cu13* companion packages
# require CUDA >= 13.0 — the host driver 566.07 only supports CUDA 12.7.
# Torch 2.1.2+cu121 bundles its own CUDA 12.1 libs and works fine with
# the 12.7 driver (CUDA is backward-compatible).
RUN --mount=type=cache,target=/root/.cache/pip \
    echo "torch==2.1.2+cu121" > /tmp/torch-pin.txt && \
    pip install \
    ${PIP_INDEX_URL:+--index-url ${PIP_INDEX_URL}} \
    ${PIP_TRUSTED_HOST:+--trusted-host ${PIP_TRUSTED_HOST}} \
    --timeout 600 \
    --no-cache-dir \
    --constraint /tmp/torch-pin.txt \
    -r requirements.txt
# ---- Stage 2: Runtime ----
FROM python:3.11-slim AS runner

WORKDIR /app

ARG APT_MIRROR
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Runtime system dependencies only
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    set -eux; \
    if [ -n "${APT_MIRROR:-}" ]; then \
        sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources && \
        sed -i "s|http://security.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    else \
        sed -i "s|http://deb.debian.org|https://deb.debian.org|g" /etc/apt/sources.list.d/debian.sources && \
        sed -i "s|http://security.debian.org|https://security.debian.org|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\nAcquire::ForceIPv4 "true";\n' > /etc/apt/apt.conf.d/80-medisim-retries; \
    for attempt in 1 2 3; do \
        apt-get update && \
        apt-get install -y --no-install-recommends --fix-missing \
            libglvnd0 \
            libglx0 \
            libglib2.0-0t64 \
            libsm6 \
            libxext6 \
            libxrender1 \
            libgomp1 && \
        break; \
        echo "APT install attempt ${attempt} failed; retrying..." >&2; \
        rm -rf /var/lib/apt/lists/*; \
        sleep 5; \
    done; \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY backend/ .

# Create necessary directories
RUN mkdir -p /tmp/uploads /app/ai-models /app/custom-models /app/static

# Copy custom-trained MONAI UNet weights to a path NOT overridden by volumes
# (/app/ai-models is mounted as a named volume in docker-compose, which
#  would hide files COPY'd there at build time)
COPY unet.pth /app/custom-models/unet.pth

# TotalSegmentator pretrained weights dir (~2GB, downloaded on first use)
# Cache them so they persist across container restarts
ENV TOTALSEGMENTATOR_DIR=/app/ai-models/totalsegmentator
RUN mkdir -p ${TOTALSEGMENTATOR_DIR}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

EXPOSE 8000

# Run with uvicorn (2 workers — one for background tasks, one to stay responsive)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
