# ============================================================
# CT Simulator - Backend Dockerfile
# ============================================================
# Multi-stage build: Python deps → runtime
# ============================================================

# ---- Stage 1: Dependencies ----
FROM python:3.11-slim AS builder

WORKDIR /app

# System dependencies for medical imaging libraries (using Tsinghua mirror)
RUN sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|http://security.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (offline from local cache)
COPY backend/requirements.txt .
COPY pip_cache/ /pip_cache/
RUN pip install --no-cache-dir --timeout 600 --no-index --find-links=/pip_cache \
        torch==2.1.2 torchvision==0.16.2 && \
    pip install --no-cache-dir --timeout 300 --no-index --find-links=/pip_cache \
        -r requirements.txt

# ---- Stage 2: Runtime ----
FROM python:3.11-slim AS runner

WORKDIR /app

# Runtime system dependencies only (using Tsinghua mirror)
RUN sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|http://security.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY backend/ .

# Create necessary directories
RUN mkdir -p /tmp/uploads /app/ai-models /app/static

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

EXPOSE 8000

# Run with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
