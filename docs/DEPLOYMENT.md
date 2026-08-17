# Deployment

## Requirements

- Docker Engine 24+ with Compose v2
- 4 GB RAM minimum, 8 GB recommended (the AI worker is capped at 2 GB)
- ~1 GB of disk for model weights, plus space for uploads and the database

## First deploy

```bash
git clone https://github.com/BoyapatiBharadwaj/Merit.Ai.git
cd Merit.Ai
cp .env.example .env
```

Compose refuses to start until `SECRET_KEY`, `POSTGRES_PASSWORD`, and
`REDIS_PASSWORD` are set. Generate each:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # the two passwords
```

Then set the deployment's real values:

```ini
ENVIRONMENT=production
CORS_ORIGINS=https://exams.your-institution.edu
APP_BASE_URL=https://exams.your-institution.edu
SEED_ADMIN_EMAIL=admin@your-institution.edu
SEED_ADMIN_PASSWORD=<a strong password, shown once in the logs>
```

`ENVIRONMENT=production` makes `core-api` refuse to boot on a placeholder
`SECRET_KEY`, a key shorter than 32 characters, or an empty `CORS_ORIGINS`. That
is a feature: a misconfigured deployment fails at startup rather than after the
first candidate signs in.

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f core-api
```

The first administrator's seeded password is printed once, on the first boot
only. Capture it, sign in, and change it.

## Coding-question sandbox

`core-api` shells out to the `docker` CLI to run each test case in an ephemeral,
network-disabled container on the **host's** daemon — there is no nested daemon.
The socket is mounted read-write, so `core-api`'s non-root user needs the host's
`docker` group GID:

```bash
getent group docker | cut -d: -f3     # e.g. 998
```

Put that in `.env` as `DOCKER_GID`. Without it every code-runner request fails
with "permission denied" on the socket.

Pre-pull the sandbox images so the first candidate's "Run" is not slowed by an
image pull:

```bash
docker pull python:3.11-slim
docker pull node:20-slim
```

If `docker` is unavailable, `code_runner_service.is_available()` returns `False`
and coding questions report themselves as ungraded rather than crashing.

Set `CODE_EXECUTION_ENABLED=false` to turn the feature off entirely.

## TLS

The bundled `proxy` listens on plain HTTP on 8080 and publishes 80. The app's own
security headers deliberately omit HSTS so the TLS-terminating layer can add it.
Two supported shapes:

**Terminate in front of the stack** — a load balancer, cloud ingress, or an
existing nginx. Forward to the proxy's published port and ensure `X-Forwarded-For`
and `X-Forwarded-Proto` are set. Confirm `TRUSTED_PROXY_IPS` covers the
forwarder's address, or rate limiting will bucket every candidate together.

**Terminate in the bundled proxy** — mount certificates into the `proxy`
container and uncomment the TLS server block in `deploy/nginx/nginx.conf`.

## Scaling

| Knob | Where | Notes |
|---|---|---|
| `WEB_CONCURRENCY` | `core-api` | uvicorn workers. Rule of thumb `(2 × cores) + 1`, capped by the database |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | `core-api` | Total Postgres connections are `(pool + overflow) × WEB_CONCURRENCY`. Postgres allows 100 by default |
| `JOB_WORKER_REPLICAS` | `worker` | RQ workers claim jobs atomically; more replicas is pure throughput |
| `CODE_EXECUTION_MAX_CONCURRENT` | `core-api` | Sandbox containers running at once. Start near `host cores / CODE_EXECUTION_CPUS` |

At the defaults, four uvicorn workers hold 60 Postgres connections — comfortable.
Eight would not be.

**`scheduler` must stay at one replica.** Each pass takes a Redis lock first, so a
second replica would be safe but pointless; the single-replica pin makes "only one
of these runs at a time" true by construction.

## Migrations

`RUN_MIGRATIONS_ON_START` defaults to `true`, which is convenient and the wrong
default for a mature production deployment — a rollback then depends on a schema
that has already moved. Once you have a deploy pipeline, set it to `false` and run
migrations as an explicit, backed-up step:

```bash
docker compose run --rm core-api alembic upgrade head
```

`scheduler` and `worker` never run migrations; `core-api` owns the schema.

## Backups

Everything durable is in Postgres. Redis holds only ephemeral state — losing it
costs in-flight OTP codes and queued jobs, never a user, exam, result, or an
email's delivery history.

```bash
docker compose exec -T postgres pg_dump -U merit_ai merit_ai | gzip > backup.sql.gz
```

Also back up the `uploads` volume: face images, ID cards, and violation
screenshots are the evidence behind every reviewer decision.

Restore:

```bash
gunzip -c backup.sql.gz | docker compose exec -T postgres psql -U merit_ai -d merit_ai
```

## Database access

`postgres` publishes no host port on purpose. To reach it:

```bash
docker compose exec postgres psql -U merit_ai -d merit_ai
```

Or open a temporary tunnel for a GUI client:

```bash
docker run --rm -d --name pgproxy --network meritai_data-net -p 15432:5432 \
  alpine/socat tcp-listen:5432,fork,reuseaddr tcp-connect:postgres:5432
# connect to localhost:15432, then:
docker rm -f pgproxy
```

## Health and observability

Every service defines a Docker health check. `core-api` exposes
`GET /api/health`, which deliberately never touches the database — it answers
"is this process alive", not "is the whole stack well", so a database blip does
not cause an orchestrator to kill an otherwise healthy container.

```bash
docker compose ps                       # health status per service
docker compose logs -f core-api worker  # follow
```

Each request carries an `X-Request-ID`, stamped by `core-api` when the proxy has
not already set one.

## Windows helpers

`deploy.ps1` validates `.env` against `.env.example`, rebuilds, waits for health,
and reports what is actually running. `-NoBuild` restarts without rebuilding;
`-Clean` rebuilds ignoring the layer cache.

`check-frontend.ps1` verifies the `frontend` container is serving the current
build rather than a stale one.

## Upgrading

```bash
git pull
docker compose up --build -d
```

Images rebuild, `core-api` migrates on boot (unless you disabled that), and the
named volumes keep the database and model weights. Use `docker compose down -v`
only when you intend to delete them.
