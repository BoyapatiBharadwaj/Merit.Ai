# Security

## Reporting a vulnerability

Please do not open a public issue. Report privately through GitHub's
[Security Advisories](https://github.com/BoyapatiBharadwaj/Merit.Ai/security/advisories/new)
on this repository, or email the maintainer directly.

Include the affected component, reproduction steps, and impact. You can expect an
acknowledgement within a few days and an assessment of severity and remediation
plan after that.

## Security posture

What this project does by design, so a reviewer knows what to check.

**Secrets**

- `.env` is gitignored; only `.env.example` with placeholders is committed.
- `ENVIRONMENT=production` refuses to boot on a placeholder `SECRET_KEY`, a key
  shorter than 32 characters, or an empty `CORS_ORIGINS`.
- `REDIS_PASSWORD` is passed separately from `REDIS_URL` so it never appears
  whole in a log line.
- Compose refuses to start without `SECRET_KEY`, `POSTGRES_PASSWORD`, and
  `REDIS_PASSWORD`.

**Authentication**

- Passwords are bcrypt-hashed and cannot be read back by anyone, including an
  administrator or a database dump.
- A password change stamps `password_changed_at`, which revokes every existing
  session for that account.
- Examiner accounts are provisioned with an unusable random secret and a
  single-use activation link. Passwords are never emailed.
- An admin-set password flags the account `must_change_password` and shows the
  owner a banner saying somebody else knows their credential.
- Attempt-scoped tokens are separate from session tokens and expire with the
  attempt deadline plus a grace window.

**Network isolation**

- Five Docker networks. The frontend container has no path to Postgres, Redis, or
  the AI worker; the AI worker reaches neither the database nor Redis; only the
  reverse proxy is published to the host.
- Postgres and Redis publish no host ports.
- Every container runs as a non-root user.

**Rate limiting and abuse**

- Redis-backed fixed-window limits, shared across every worker and replica, on
  authentication and proctoring endpoints.
- Fails **closed** with a `503` when Redis is unreachable, rather than letting
  every request through unthrottled.
- `X-Forwarded-For` is read rightmost-untrusted and only from peers inside
  `TRUSTED_PROXY_IPS`, so a client cannot spoof its way into a fresh budget.

**Code execution**

- Candidate code runs in an ephemeral `--network none` container with memory,
  CPU, and process-count caps, and is destroyed after each test case.
- Code is base64-encoded and handed to a trusted bootstrap script rather than
  interpolated into a shell string.
- Disables itself cleanly when the Docker socket is unavailable.

**Biometric data**

- Face embeddings, face images, ID cards, and violation screenshots are stored
  with recorded consent and a consent version.
- `BIOMETRIC_RETENTION_DAYS` drives automatic deletion after a candidate's last
  attempt. It defaults to `0` (never), so nothing is destroyed by accident.
- Erasure and re-verification are separate actions: erasure removes data on
  request; re-verification keeps the stored images, because they are the evidence
  that prompted the doubt.
- Every administrative change to a candidate record is written to the activity
  log with actual before/after values.

## Known limitations

- The JWT is a single long-lived access token; there are no refresh tokens yet, so
  revocation before expiry relies on the `password_changed_at` check.
- The bundled reverse proxy serves plain HTTP and expects TLS to be terminated in
  front of it or configured explicitly. The application deliberately omits HSTS so
  the terminating layer owns it.
- Anti-spoofing without a trained model installed is advisory only and raises no
  violation on its own.

## Supported versions

This project is under active development. Security fixes are applied to `main`.
