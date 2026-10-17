# Deploy the historical dashboard on Render

The repository is prepared for a Docker web service backed by PostgreSQL 17. This guide does not
create resources. Applying `render.yaml` creates a publicly accessible service; get the project
owner's explicit approval before doing so. No training, ingestion or evaluation runs on Render.

## 1. Create the database

In Render, create a **PostgreSQL** instance in **Frankfurt**, explicitly selecting **version 17**.
Use a paid database for a lasting portfolio deployment: free databases expire after 30 days.
Review the current [pricing](https://render.com/pricing) before creating paid resources.
The web Blueprint uses a free instance initially (idle spin-down and cold starts apply); upgrade
the web service if its measured memory use or availability requires it.

The database and web service must belong to the same workspace and region. The app uses the
**internal hostname**, while the one-time import from your computer uses the **external hostname**.
Temporarily allow your computer's public IP in the database Networking settings for the import.

## 2. Export the existing local analytical database

Docker Desktop and the local database must be running. From the repository root:

```bash
make db-up
make export-app-db
```

This writes `app.dump` (custom pg_dump format, file permissions 0600, ignored by Git). An existing
file is never overwritten. If a verified export already exists, use it directly in step 3 instead
of exporting again. To choose another file:

```bash
uv run python -m delivery_risk.deployment export /tmp/delivery-risk.dump
```

Only `meta`, `mart`, `evaluation`, `feature` and `quality` are included, without local owners or
grants. No `raw`, `staging`, `core`, model binaries or CSV files are required by the dashboard.
The export still contains order identifiers, analytical records, provenance and quality diagnostics
(including quarantined source records); keep it private. Stop any local pipeline publication while
exporting so the snapshot corresponds to the finished run.

## 3. Restore and create a dedicated reader

Install PostgreSQL client tools version **17 or later** (`pg_restore` must be on PATH).
On macOS with Homebrew, install them if needed and select them in the current terminal:

```bash
brew install postgresql@17
export PATH="$(brew --prefix postgresql@17)/bin:$PATH"
pg_restore --version
```

The version output must show 17 or later. There is no need to start a Homebrew PostgreSQL server.
Use the database owner's credentials from Render's database Info page. Set the destination
**in your local terminal**, using the external hostname and the actual database/user names:

```bash
export PGHOST=your-external-hostname
export PGPORT=5432
export PGDATABASE=your-render-database-name
export PGUSER=your-render-database-owner
export PGSSLMODE=require
# Type the owner password at the blank prompt; input is hidden and stays out of shell history.
read -s PGPASSWORD
export PGPASSWORD

uv run python -m delivery_risk.deployment restore app.dump
uv run python -m delivery_risk.deployment reader --role dr_dashboard
unset PGPASSWORD
```

Alternatively, use a private `.pgpass` / `PGPASSFILE` with permissions 0600. Never put connection
URLs containing passwords into commands, commits or chat. The reader command prompts for a new
password twice; store it in your password manager for the web service configuration.

Restore refuses a destination that already contains any of the five application schemas and uses
one transaction, with no cleanup or replacement of existing objects. If the reader step fails after
a successful restore, rerun **only** the reader step after fixing the error. It creates a new role
and refuses to modify an existing role. This role receives CONNECT, schema USAGE and table SELECT
only, with read-only transactions by default. It does not receive database-owner credentials.
Grants cover the restored snapshot; refreshes should use a new database and reader, validate them,
then switch the web service settings.

After import, remove the temporary external IP allowance (an empty allow list disables external
database connections); the app continues to use the internal network.

## 4. Create the web service after publication approval

Push the reviewed code changes to the GitHub repository that Render will build. In Render, choose
**New → Blueprint**, connect that repository and review `render.yaml`. The Blueprint provisions only
the web service; the database is deliberately created and populated first.

Supply the values requested by Render:

| Variable | Value |
|---|---|
| `DR_DB_HOST` | Database **internal** hostname from Render |
| `DR_DB_NAME` | Actual restored database name |
| `DR_READER_PASSWORD` | Password just created for `dr_dashboard` |

The Blueprint sets `DR_MODE=real`, `DR_DB_PORT=5432`, `DR_DB_SSLMODE=require`,
`DR_READER_USER=dr_dashboard` and `WEB_CONCURRENCY=1`. If you selected a different reader name,
update `DR_READER_USER` accordingly. Do not add admin or pipeline credentials to this service.
Render supplies `PORT`; Gunicorn binds to `0.0.0.0:$PORT`. There is no artifact volume to configure.

Automatic deploys are disabled. The initial Blueprint application still builds and deploys once;
later releases require **Manual Deploy** in Render. No GitHub Actions deployment is configured.
Blueprint configuration sync is a separate setting: to also apply future `render.yaml` changes
manually, set **Auto Sync** to **No** on the Blueprint's Settings page.
The Docker build context excludes local secrets, exports, datasets, reports and model artifacts.

## 5. Verify the deployed snapshot

- Open `/health`: expect HTTP 200 and `{"status":"ok"}`. This is a process liveness check; it
  intentionally does **not** verify the database, reader grants or data completeness.
- Open all three pages, change a period/region filter and select a historical review day. Check that
  real results load and match the local app. A successful health check alone is insufficient.
- Verify that “At approval” hides outcomes, that the model/quality tables load, and that dataset
  attribution and its CC BY-NC-SA link appear in the footer.
- If data fails to load, check the internal hostname, name, reader password and role grants in the
  Render settings. Read service logs without copying credentials into an issue.

The result remains a retrospective, non-commercial study. The repository's code licence and the
Olist data licence have separate scopes; the footer names the source and describes the adaptations.

## Local use

`make dashboard` and `make dashboard-docker` still use port 8050. Stop one before starting the other.
Leave `DR_DB_NAME` and `DR_DB_SSLMODE` empty for local PostgreSQL. The database-name override applies
to real mode only, so test/fixture resets cannot be redirected to the managed database by it.
Do not run the local pipeline or test suite with cloud database connection settings.

## References

Configuration checked on 2026-10-17 against Render's [Blueprint specification](https://render.com/docs/blueprint-spec),
[Postgres connections](https://render.com/docs/postgresql-creating-connecting),
[free-instance limitations](https://render.com/docs/free), and PostgreSQL 17's
[pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html) /
[pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html) documentation.
