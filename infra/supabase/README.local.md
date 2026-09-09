# Self-hosted Supabase (local) — trimmed

Vendored from `supabase/supabase` → `docker/` and trimmed via
`docker-compose.override.yml` (set as `COMPOSE_FILE` in `.env`).

**Running services:** `db`, `studio`, `meta`, `api-gw` (Envoy), `auth`, `rest`.
**Skipped** (behind the `full` profile): `realtime`, `storage`, `imgproxy`,
`functions`, `supavisor`. Bring the rest up with `docker compose --profile full up -d`.

## Use

```bash
cd infra/supabase
docker compose up -d           # ~6 containers, ~650 MB
```

- **Dashboard:** http://127.0.0.1:8000  — basic auth `DASHBOARD_USERNAME` /
  `DASHBOARD_PASSWORD` from `.env`. (Also http://127.0.0.1:3001 straight to Studio.)
- **Postgres:** `127.0.0.1:5432`, user `postgres`, `POSTGRES_PASSWORD` from `.env`.
- Inside Docker it's reachable as **`db:5432`** on the `supabase_default` network —
  the app backends join that network (see the root compose files) and connect with
  `SIGNAL_DATABASE_URL=postgresql://postgres:<pw>@db:5432/postgres`.

## App schema

The credit system lives in the `scratchpad` schema. Migrations
(`backend/migrations/*.sql`) run automatically when the backend starts, or:

```bash
docker compose -f docker-compose.test.yml exec scratchpad-test-backend \
  python -m backend.credits migrate
```

## Secrets

`.env` is gitignored and holds freshly generated secrets (`utils/generate-keys.sh`
+ `utils/add-new-auth-keys.sh`). `volumes/db/data/` (the Postgres data dir) is
gitignored too.

## Moving to hosted Supabase later

`pg_dump --schema=scratchpad --no-owner --no-privileges` this database, restore
into the hosted project, and point `SIGNAL_DATABASE_URL` at the hosted pooler
URL (`...:6543`, transaction mode — `asyncpg` already runs with
`statement_cache_size=0`).
