# ============================================================
# CT Simulator - Frontend Dockerfile
# ============================================================
# Multi-stage build: node deps → build → nginx serve
# ============================================================

# ---- Stage 1: Dependencies ----
FROM node:20-slim AS deps

WORKDIR /app

ARG NPM_REGISTRY=https://registry.npmmirror.com

# Copy package.json only. The lockfile is Windows-generated, so let npm
# resolve Linux-specific optional dependencies during the image build.
COPY package.json ./

# Install dependencies with a cacheable npm directory.
RUN --mount=type=cache,target=/root/.npm \
    npm config set registry ${NPM_REGISTRY} && \
    npm install --timeout=300000 --no-audit --no-fund --legacy-peer-deps

# ---- Stage 2: Build ----
FROM node:20-slim AS builder

WORKDIR /app

ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

COPY --from=deps /app/node_modules ./node_modules
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
