# ============================================================
# CT Simulator - Frontend Dockerfile
# ============================================================
# Multi-stage build: node deps → build → nginx serve
# ============================================================

# ---- Stage 1: Dependencies ----
FROM node:20-alpine AS deps

WORKDIR /app

# Copy package files
COPY frontend/package.json ./
COPY frontend/yarn.lock* ./

# Install dependencies (frozen lockfile for reproducibility)
RUN yarn install --frozen-lockfile --network-timeout 300000

# ---- Stage 2: Build ----
FROM node:20-alpine AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ .

# Build the application
RUN yarn build

# ---- Stage 3: Serve ----
FROM nginx:alpine AS runner

# Copy nginx configuration
COPY docker/frontend/nginx.conf /etc/nginx/nginx.conf

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://localhost:80/health || exit 1

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
