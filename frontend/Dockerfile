# =============================================================================
# Frontend Dockerfile - Game Boosting Platform
# Multi-stage build: Node.js build -> Nginx Alpine
# =============================================================================

# =============================================================================
# Stage 1: Build
# =============================================================================

FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
# Use npm ci when lockfile exists; fallback to npm install for repos without lockfile.
RUN if [ -f package-lock.json ]; then npm ci --include=dev; else npm install; fi

# Copy source code
COPY . .

# Build the application
RUN npm run build


# =============================================================================
# Stage 2: Production
# =============================================================================

FROM nginx:alpine AS production

# Remove default nginx config
RUN rm -rf /etc/nginx/conf.d/*

# Copy custom nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Create non-root user
RUN addgroup -g 1001 -S appgroup && \
    adduser -S -D -H -u 1001 -h /var/cache/nginx -s /sbin/nologin -G appgroup appuser && \
    chown -R appuser:appgroup /var/cache/nginx && \
    chown -R appuser:appgroup /var/log/nginx && \
    chown -R appuser:appgroup /etc/nginx/conf.d && \
    touch /var/run/nginx.pid && \
    chown -R appuser:appgroup /var/run/nginx.pid

# Expose port
EXPOSE 80

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:80/ || exit 1

# Run nginx
CMD ["nginx", "-g", "daemon off;"]
