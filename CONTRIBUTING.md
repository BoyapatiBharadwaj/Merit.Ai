# Contributing

## Getting set up

See [Local development](README.md#local-development) in the README. The short
version:

```bash
cd backend && pip install -r requirements-dev.txt && pytest
cd frontend && npm install && npm run lint && npm test
```

Both must pass before you open a pull request.

## Branches and commits

Branch off `main`:

```
feat/exam-section-reorder
fix/autosave-retry-backoff
docs/deployment-tls
```

Write commit subjects in the imperative mood, under ~72 characters, describing
what the change does rather than what you did:

```
Add section rename endpoint for draft exams
Fix rate-limit bucket when behind two proxies
```

If the body needs to explain *why*, put it in the body. Keep that reasoning out
of the source files — code comments should say what the code does now, not the
history of how it got here.

## Code conventions

**Backend (Python)**

- Follow the existing layering. Routers parse requests and enforce auth;
  services hold business logic and own transaction boundaries; repositories run
  queries and raise no HTTP errors. A router must not touch the ORM directly.
- Type-annotate function signatures. Pydantic schemas define every request and
  response contract.
- Line length 100. 4-space indents.
- Docstrings are one or two sentences. Comments explain non-obvious behaviour in
  a line or two — not design history, not alternatives considered.

**Frontend (JavaScript / React)**

- Function components with hooks. `ErrorBoundary` is the one class component,
  because `componentDidCatch` has no hooks equivalent.
- API calls go through `src/lib/api.js`. Do not call `fetch` from a component.
- Tailwind utilities for styling; shared class strings live in `src/lib/ui.js`.
- `npm run lint` must be clean.

**Both**

- No commented-out code. Delete it — git remembers.
- No `TODO` / `FIXME` on `main`. Open an issue instead.
- No `console.log` or bare `print()` in application code.

## Database changes

Every schema change needs an Alembic migration:

```bash
cd backend
alembic revision -m "add section display order"
# edit the generated file, implement upgrade() and downgrade()
alembic upgrade head
```

Write a real `downgrade()`. Migrations run against PostgreSQL in CI, so verify
anything dialect-specific there rather than only on SQLite.

## Tests

New behaviour needs a test. Add it to the module that matches the area (see
[docs/TESTING.md](docs/TESTING.md)) and use the existing fixtures rather than
constructing clients by hand.

Assert on behaviour visible through the API, not on internal call sequences, so a
refactor that preserves behaviour does not break the suite.

Tests that need real row locks or Postgres enum behaviour carry
`@pytest.mark.postgres`.

## Pull requests

Include what changed and why, how you verified it, and any configuration or
migration a deployer must apply. Screenshots for UI changes.

CI runs the SQLite suite and the Postgres migration suite on every pull request.
Both must be green.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
