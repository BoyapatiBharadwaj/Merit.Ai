# syntax=docker/dockerfile:1
# Merit.Ai frontend -- multi-stage: Vite build, then static assets on unprivileged nginx.

FROM node:20-alpine AS build
WORKDIR /build

# Separate the dependency install from the source copy so `docker build` only re-runs the
# install when package*.json actually change, not on every source edit.
COPY package.json package-lock.json ./
# Cache mount: npm's download cache persists across builds on this Docker host (keyed by the
# mount target, never part of the committed layer), so re-running this after a package.json edit
# only fetches what actually changed instead of the whole tree again.
RUN --mount=type=cache,target=/root/.npm \
    npm install --legacy-peer-deps

COPY . .
RUN npm run build

# ------------------------------------------------------------------------------
# --- Runtime: unprivileged nginx serving the built dist/ with a SPA fallback ---
# ------------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# Full config (not a conf.d fragment) -- see frontend/nginx.conf for why:
# it moves the pid file and every writable temp path out of root-owned
# directories so this can run as the built-in, unprivileged `nginx` user.
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /build/dist /usr/share/nginx/html

RUN mkdir -p /tmp/client_temp /tmp/proxy_temp /tmp/fastcgi_temp /tmp/uwsgi_temp /tmp/scgi_temp \
    && chown -R nginx:nginx /usr/share/nginx/html /tmp

USER nginx
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O- http://127.0.0.1:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
