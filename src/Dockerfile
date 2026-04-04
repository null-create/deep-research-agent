FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files first
COPY package*.json ./

# Install dependencies
RUN npm install --force

# Copy configuration files
COPY tsconfig*.json ./
COPY vite.config.ts ./
COPY tailwind.config.js ./
COPY postcss.config.js ./
COPY index.html ./

# Copy source code
COPY . .

# Build with verbose output
RUN npm run build

# Debug: List what was built
RUN echo "=== Build output ===" && \
  ls -la dist/ && \
  echo "=== Assets ===" && \
  ls -la dist/assets/ || true

# Production stage
FROM nginx:alpine

# Copy built assets
COPY --from=builder /app/dist /usr/share/nginx/html

# Install gettext for envsubst (used by nginx template processing)
RUN apk add --no-cache gettext

# Copy nginx config as a template — nginx's docker-entrypoint.sh will run
# envsubst on *.conf.template files in /etc/nginx/templates/ at startup,
# substituting NGINX_BACKEND_URL and NGINX_FILE_HANDLER_URL before nginx starts.
COPY nginx.conf /etc/nginx/templates/default.conf.template

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]