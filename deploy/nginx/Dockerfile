# syntax=docker/dockerfile:1
# ==============================================================================
# Reverse proxy -- the single public entrypoint for the stack. See nginx.conf
# for the routing rules; this Dockerfile only sets up an unprivileged runtime
# for it (no root, no privileged port bound from inside the container).
# ==============================================================================
FROM nginx:1.27-alpine

RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/nginx.conf

RUN mkdir -p /tmp/client_temp /tmp/proxy_temp /tmp/fastcgi_temp /tmp/uwsgi_temp /tmp/scgi_temp \
    && chown -R nginx:nginx /tmp

USER nginx
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O- http://127.0.0.1:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
