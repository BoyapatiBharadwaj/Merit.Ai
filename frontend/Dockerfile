# syntax=docker/dockerfile:1
# ==============================================================================
# Merit.Ai Frontend -- React/Vite SPA, built once and served as static files.
#
# There is no server-side rendering and no runtime Node process in the final
# image: `npm run build` produces a static dist/ that any web server can
# serve, so the runtime stage is plain nginx, not node. API calls the app
# makes at runtime go to whatever origin it's loaded from (see
# frontend/src/lib/api.js's relative BASE_URL) -- the reverse-proxy container
# in front of this one is what makes `/api/*` reach the Core API (see
# deploy/nginx/nginx.conf), so nothing here needs to know the Core API's
# address at build time.
# ==============================================================================

FROM node:20-alpine AS build
WORKDIR /build

# Separate the dependency install from the source copy so `docker build`
# only re-runs the install when package*.json actually change, not on every
# source edit.
#
# `npm ci` (strict, lockfile-only) is what this used to run, but
# package-lock.json had drifted out of sync with package.json -- the ESLint
# tooling (eslint, @eslint/js, eslint-plugin-react, eslint-plugin-react-hooks,
# globals) was added to package.json without ever being installed locally to
# update the lock, so it was simply absent from package-lock.json. `npm ci`
# refuses to reconcile that gap itself, and once it tried to resolve those
# packages fresh it also hit a real peer-dependency conflict (eslint-plugin-
# react-hooks@4.x only supports ESLint up to v8; this project is on ESLint 9)
# -- since fixed by bumping it to ^5.0.0. `npm install --legacy-peer-deps`
# repairs a lockfile like this instead of just refusing to build from it.
COPY package.json package-lock.json ./
# Cache mount: npm's download cache persists across builds on this Docker
# host (keyed by the mount target, never part of the committed layer), so
# re-running this after a package.json edit only fetches what actually
# changed instead of the whole tree again.
RUN --mount=type=cache,target=/root/.npm \
    npm install --legacy-peer-deps

COPY . .
RUN npm run build

# ------------------------------------------------------------------------------
# Runtime -- unprivileged nginx serving the built dist/ with a SPA fallback.
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
