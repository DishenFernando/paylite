# Cloud server / GitHub deployment

**Want to run this on a remote Ubuntu server without running it locally? Start with [SERVER_DEPLOYMENT.md](SERVER_DEPLOYMENT.md).** The server uses `compose.prod.yaml` (Caddy + FastAPI + PostgreSQL), while the original `compose.yaml` below remains for local development. Do not commit `.env`.

---

# PayLite — Team D | Complete Dockerized Digital Wallet

**Full-stack assignment/demo app: browser UI + FastAPI REST backend + PostgreSQL relational DB + Adminer browser DB viewer.** No real payments are processed.

## Start in VS Code (1 command)

1. Install **Docker Desktop** (enable Linux containers/WSL 2 on Windows) and **VS Code**.
2. Extract this ZIP, open `PayLite_Docker_PostgreSQL` in VS Code, and open **Terminal > New Terminal**.
3. Create a local demo environment file (`Copy-Item .env.example .env` in PowerShell, or `cp .env.example .env` on Linux) and from the project root run:

```bash
docker compose up --build -d
```

The `.env.example` template contains **local demonstration credentials only**; copy it to `.env` for local testing. The private `.env` file is intentionally not included in the ZIP or GitHub repository. On a shared machine or before deployment, edit it and set a strong password first. If you add new migrations after the DB volume has already been initialized, they do not run automatically. This schema init runs only on the first database creation.

### Open the three interfaces

| URL | What it does |
|---|---|
| **http://localhost:8000** | Web frontend: registration, wallet dashboard, top-up, transfer, statement, admin reversals |
| **http://localhost:8000/docs** | Interactive FastAPI Swagger API documentation |
| **http://localhost:8081** | **Adminer: browse PostgreSQL database, see actual rows, run SQL** |

### Open the database in Adminer

At **http://localhost:8081**, use these fields:

| Adminer field | Value |
|---|---|
| System | `PostgreSQL` |
| Server | `db` |
| Username | `paylite` |
| Password | `paylite_local_demo_2026` *(from `.env`, modify together if changed)* |
| Database | `paylite` |

Click **Login**. You will see `users`, `wallets`, `ledger_entries`, `transfers`, and `sessions`.
Select a table, then **Select data** to inspect its rows. The **SQL command** tab lets you run the queries in `DATABASE_GUIDE.md`.

**Important:** `db` is the Docker Compose service hostname. Do not enter `localhost` into Adminer's Server field; that would mean the Adminer container rather than the PostgreSQL container.

### Demo accounts

| Role | Mobile | Password | Wallet PIN | Starting balance |
|---|---|---|---|---|
| Alice (Customer) | `0771111111` | `DemoPass123!` | `1234` | Rs. 1,000.00 |
| Bob (Customer) | `0772222222` | `DemoPass123!` | `5678` | Rs. 200.00 |
| Admin | `0779999999` | `AdminPass123!` | `9999` | Rs. 0.00 |

Use Alice to top up and transfer to Bob; then log in as Admin to reverse a transfer.
The demo accounts are inserted only when missing; balances and history are **never reset** simply by restarting containers. This is a **new PostgreSQL database**; data from the earlier SQLite ZIP is not automatically imported.

## Architecture

```text
Your browser
  ├─ http://localhost:8000 ──► app (FastAPI container, port 8000)
  │                                   │
  │                                   └─► db (PostgreSQL 17, internal 5432)
  │                                                │
  │                                         Docker named volume
  │                                         paylite_postgres_data
  └─ http://localhost:8081 ──► adminer (DB browser)
                                          │
                                          └─► db (internal hostname)
```

- **Frontend:** Plain responsive HTML/CSS/JavaScript served by FastAPI; no npm/build tooling.
- **Backend:** Python 3.12, FastAPI, REST/JSON, database transactions, authentication.
- **Database:** PostgreSQL 17, SQL init script with keys, constraints, indexes, append-only ledger trigger.
- **Database browser:** Adminer, bound to `127.0.0.1:8081` rather than exposed publicly.
- **Networking:** Internal Docker bridge; PostgreSQL port is **not published** to host.
- **Persistence:** The database survives `docker compose down`, stopping/restarting Docker, and app rebuilds.

## Main features

- Register with unique mobile, PBKDF2 password hash and PIN hash; auto-create a zero-balance wallet.
- Login with opaque bearer session token (SHA-256 token hash stored in DB); 3 failed password attempts lock the account for 5 minutes.
- Simulated top-ups Rs. 1–5,000, transactional wallet and matching ledger entry.
- P2P payments by mobile, PIN verification, insufficient funds check, no self-transfer.
- `Idempotency-Key` header for a retry; same key + same recipient + amount returns same transfer without a new debit.
- Daily outgoing limit Rs. 10,000 (UTC day; config in `.env`).
- Atomic transfer with consistent ascending row lock order; sender debit and recipient credit both commit or both roll back.
- Date-filtered statements, CSV export, running balance and counterparty.
- Admin-only reversals: exactly one, with compensating ledger entries and a sufficient recipient balance requirement.
- SQL trigger prohibits updating/deleting entries in the ledger; ledger rebuilding check exposed via `/api/wallet/integrity`.
- JSON request logs with correlation ID; no PIN, password, bearer token, or query strings logged.
- Custom health checks and automated end-to-end tests.

## API endpoints

| Verb | Endpoint | Purpose |
|---|---|---|
| POST | `/api/auth/register` | Register |
| POST | `/api/auth/login` | Login/token |
| POST | `/api/auth/logout` | Logout |
| GET | `/api/wallet` | Balance and limit |
| POST | `/api/wallet/topup` | Top-up |
| GET | `/api/users` | Payee directory |
| POST | `/api/transfers` | PIN-confirmed transfer (`Idempotency-Key` header) |
| GET | `/api/statement` | Statement; optional `from_date`, `to_date` |
| GET | `/api/statement/export` | CSV statement |
| GET | `/api/wallet/integrity` | Verify ledger vs wallet balance |
| GET | `/api/admin/transfers` | List transfers (admin) |
| POST | `/api/admin/transfers/{id}/reverse` | Reverse (admin) |
| GET | `/api/health` | API and DB health |

## Useful commands

```bash
# See what containers are running
docker compose ps

# Watch backend output / errors
docker compose logs -f app

# Run end-to-end integration test suite (creates separate test users in DB)
docker compose --profile test run --build --rm tests

# Browse PostgreSQL in terminal (optional)
docker compose exec db psql -U paylite -d paylite

# Pause/stop (preserves all wallet/ledger data)
docker compose down

# Rebuild/restart (preserves data)
docker compose up --build -d
```

**Do not run `docker compose down -v` unless you intend to delete all wallet data and create a fresh database.**

For a backup:

```bash
docker compose exec -T db pg_dump -U paylite -d paylite > paylite-backup.sql
```

For DB inspection queries see [`DATABASE_GUIDE.md`](DATABASE_GUIDE.md).

## Configuration

Settings live in `.env`: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `APP_PORT`, `ADMINER_PORT`, `SEED_DEMO`, `DAILY_TRANSFER_LIMIT`.

Edit `.env` to change them. With an **existing** PostgreSQL volume, changing `POSTGRES_USER`, `POSTGRES_PASSWORD` or `POSTGRES_DB` in `.env` does not alter the already initialized database credentials. Change DB user/password via SQL or create a new local demo volume (data loss!).

## Limitations and security note

This is an **assignment/demo** application and must **not** accept real funds. Local demo passwords and an admin SQL browser are intentionally provided for learning. Before a real deployment you would need proper secrets management, TLS, stronger account protections / abuse detection, backups and recovery testing, audit processes, production migrations, payment-grade controls and a security review. The database/admin UI ports are restricted to local host by default.
