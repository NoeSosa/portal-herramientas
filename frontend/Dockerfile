# =============================================================================
# Frontend — build de React, servido por nginx
# =============================================================================
# El build corre en su propia etapa; la imagen final solo lleva los estáticos.
# =============================================================================

# --- Etapa 1: compilar ----------------------------------------------------
FROM node:22-alpine AS constructor

WORKDIR /app

COPY package.json package-lock.json* ./

# `npm ci` si hay lockfile (reproducible), `npm install` si no.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .

RUN npm run build

# --- Etapa 2: servir ------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# Config propia: proxy a /api y cabeceras de seguridad.
COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=constructor /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1/healthz || exit 1
