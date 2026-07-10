# ============================================================
# CT Simulator - Frontend Dockerfile
# ============================================================
# Multi-stage build: node deps → build → nginx serve
# ============================================================

# ---- Stage 1: Dependencies ----
FROM node:20-slim AS deps

WORKDIR /app

ARG NPM_REGISTRY

# Copy manifest + lockfile first so dependency installs stay cacheable.
COPY package.json package-lock.json ./

# Install dependencies. The lockfile was generated on Windows, and `npm ci`
# can miss Linux-only optional native packages such as Rollup binaries in this
# setup, so use `npm install` here for cross-platform container reliability.
# If NPM_REGISTRY build-arg is set, use it as the npm registry; otherwise the
# default registry is used. Limit Node memory to avoid OOM on low-RAM hosts.
ENV NODE_OPTIONS="--max-old-space-size=2048"
RUN --mount=type=cache,target=/root/.npm \
    if [ -n "${NPM_REGISTRY}" ]; then \
        npm config set registry "${NPM_REGISTRY}"; \
    else \
        npm config set registry "https://registry.npmmirror.com"; \
    fi && \
    npm install --timeout=120000 --no-audit --no-fund --legacy-peer-deps && \
    npm install --timeout=120000 --no-audit --no-fund --legacy-peer-deps @rollup/rollup-linux-x64-gnu@4.60.4

# ---- Stage 2: Build ----
FROM node:20-slim AS builder

WORKDIR /app

ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/package*.json ./
COPY . .

# Build the application
RUN npm run build

# ---- Stage 3: Serve ----
FROM nginx:alpine AS runner

# Copy nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1:80/health || exit 1

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
