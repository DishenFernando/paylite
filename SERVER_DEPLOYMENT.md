# PayLite — GitHub → Ubuntu cloud server deployment

This package contains the **existing PayLite frontend, FastAPI backend and PostgreSQL schema**, plus a separate production-style Docker Compose configuration, Caddy reverse proxy, CI tests, private database access, and backup/update scripts. **This is a simulated-money course project, not a real payment system.**

## 0. What you need

- A GitHub account and Git installed wherever you will upload the repository (Windows VS Code is fine; **do not run the app locally**).
- An Ubuntu 24.04/22.04 VPS or other Linux cloud VM with a public IP and SSH access. Recommended for a demo: **2 vCPU, 2–4 GB RAM, 20 GB disk** (capacity is a planning estimate, not a guarantee).
- Preferably your own domain name, for example `wallet.example.com` (replace this placeholder). Point its DNS A record at the server public IPv4 address.
- Cloud security group/firewall: allow inbound SSH/22 only from your trusted IP, and inbound HTTP/80 + HTTPS/443 from the Internet. You **do not** need to open 5432, 5433, 8000, or 8081 to the Internet.

Architecture:

```text
GitHub source repository
         │ git clone / git pull (on server)
         ▼
Cloud VPS (Ubuntu + Docker Compose)
  Internet ── 80/443 ── Caddy (automatic HTTPS with domain)
                             └── FastAPI + frontend (private port 8000)
                                    └── PostgreSQL 17 (private 5432)
                                         └── Docker named volume (persistent)

DBeaver on your laptop ── SSH tunnel ── 127.0.0.1:5433 on VPS ── PostgreSQL
```

## 1. Upload to GitHub (no application runtime on your PC)

Create an **empty** repository named `paylite` at <https://github.com/new>. Do not generate a separate README/license/gitignore there (these files are already in the project). Open the extracted project folder in VS Code, then run:

```powershell
git init
git add .
git status
git commit -m "PayLite fullstack app with server deployment"
git branch -M main
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/paylite.git
git push -u origin main
```

Replace `YOUR_GITHUB_USERNAME`. GitHub sign-in is required for push. **Review `git status` first: `.env` must not be present**; `.env.production.example` is a safe sample with placeholders. If you already committed secrets, removing the file in a later commit is not enough: rotate the leaked values and remove the secret from Git history before sharing the repository.

> Alternative: use GitHub's web UI to upload the extracted folder files, preserving paths. Do not upload only the ZIP as a single file; the server needs a real source tree.

## 2. Install Docker on Ubuntu server (once)

From your computer:

```bash
ssh ubuntu@YOUR_SERVER_IP
```

Use your VPS's actual login user (on some providers this differs from `ubuntu`). Install from the **official Docker apt repository** (not the convenience install script):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOFDOCKER
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOFDOCKER
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker compose version
```

Official instructions: <https://docs.docker.com/engine/install/ubuntu/>. If your cloud server already has Docker Engine and the Compose plugin, skip this installation.

## 3. Clone GitHub repository onto the SERVER

```bash
sudo mkdir -p /opt/paylite
sudo chown "$USER":"$(id -gn)" /opt/paylite
git clone https://github.com/YOUR_GITHUB_USERNAME/paylite.git /opt/paylite
cd /opt/paylite
```

The command assumes a **public repository**. For a private repository, configure a read-only GitHub SSH deploy key on the server or use authenticated GitHub CLI, then clone via SSH. Never embed a GitHub personal access token in command-line URLs or in `.env`.

## 4. Configure server-only secrets

```bash
cp .env.production.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(36))'
nano .env
chmod 600 .env
```

Set:

```dotenv
APP_DOMAIN=wallet.your-domain.com
POSTGRES_DB=paylite
POSTGRES_USER=paylite
POSTGRES_PASSWORD=YOUR_GENERATED_RANDOM_PASSWORD
SEED_DEMO=false
DAILY_TRANSFER_LIMIT=10000
```

**Don't use** the example domain or the placeholder password. `SEED_DEMO=false` avoids creating well-known password/PIN demo accounts on the Internet. The `APP_DOMAIN` must have a DNS A record pointing at your VPS and ports 80 and 443 open for automatic TLS certificates.

**No domain yet?** For a *temporary demonstration only*, set `APP_DOMAIN=:80` and browse to `http://YOUR_SERVER_IP`. This exposes login over plaintext HTTP, so use disposable test credentials only; do not store real or sensitive data. Switch to a domain + HTTPS as soon as possible.

## 5. Start the entire remote application

On the **server**, from `/opt/paylite`:

```bash
sudo docker compose -f compose.prod.yaml up -d --build
sudo docker compose -f compose.prod.yaml ps
sudo docker compose -f compose.prod.yaml logs --tail=80 caddy app db
```

URLs (after DNS and HTTPS are configured):

- Frontend: `https://wallet.your-domain.com/`
- API: `https://wallet.your-domain.com/api/health`
- Swagger: `https://wallet.your-domain.com/docs`

Your laptop does NOT have to keep running; cloud containers have restart policies. Data lives in the server's Docker named PostgreSQL volume and survives `docker compose down` or app updates. **Never run `down -v` on your server** unless you intend to delete the database.

## 6. Create a private admin user (do not enable demo accounts publicly)

```bash
sudo docker compose -f compose.prod.yaml exec app python create_admin.py
```

The script prompts securely for a new admin name, phone, password and PIN. The created wallet starts with zero balance. Regular customers register via the public UI. No login credentials are embedded in your GitHub source or server commands.

## 7. View remote PostgreSQL in DBeaver without opening database ports

The database is bound to server loopback at `127.0.0.1:5433`, **not reachable directly from the public network**. On your own laptop run this in PowerShell (leave it open while using DBeaver):

```powershell
ssh -N -L 15433:127.0.0.1:5433 ubuntu@YOUR_SERVER_IP
```

In **DBeaver → New connection → PostgreSQL**:

| Field | Value |
|---|---|
| Host | `127.0.0.1` |
| Port | `15433` |
| Database | `paylite` |
| Username | `paylite` (or from your `.env`) |
| Password | The server's `POSTGRES_PASSWORD` |

You can inspect:

```sql
SELECT id, name, mobile, role, created_at FROM users ORDER BY created_at DESC;
SELECT u.name, u.mobile, w.balance_cents / 100.0 AS balance
FROM users u JOIN wallets w ON w.user_id=u.id;
SELECT id, status, amount_cents / 100.0 AS amount FROM transfers;
```

If you prefer Adminer, run `sudo docker compose -f compose.prod.yaml --profile tools up -d adminer` on the server and tunnel with `ssh -N -L 18081:127.0.0.1:8081 ubuntu@YOUR_SERVER_IP`, then open <http://localhost:18081> on your computer. Adminer server hostname is `db`, not `localhost`.

## 8. Push a code update → redeploy remotely

On your laptop edit the code, then:

```powershell
git add .
git commit -m "Update PayLite"
git push origin main
```

On the cloud server:

```bash
cd /opt/paylite
bash deploy/update.sh
```

This pulls the latest main branch, rebuilds the app and leaves the Postgres data volume alone. **CI** is already included in `.github/workflows/ci.yaml`: GitHub Actions runs the integration suite against a disposable PostgreSQL database on pushes and pull requests. CI does **not** automatically deploy to your server; that is intentionally a separate approval/action for a money-like application.

## 9. Backups

On the server:

```bash
cd /opt/paylite
sudo bash deploy/backup.sh
```

Creates a timestamped SQL backup under `./backups/`, ignored by Git. The backup holds user information: store securely and preferably copy to a separate encrypted backup location. Test restoring to a separate disposable database before relying on it. The supplied app is a demonstration and should not process real money.

## 10. Troubleshooting

```bash
sudo docker compose -f compose.prod.yaml ps
sudo docker compose -f compose.prod.yaml logs --tail=120 app
sudo docker compose -f compose.prod.yaml logs --tail=120 caddy
sudo docker compose -f compose.prod.yaml logs --tail=120 db
curl -v http://127.0.0.1/api/health
```

Common causes: no domain A record, ports 80/443 blocked in cloud firewall, public port 80/443 already occupied by another service, `.env` still contains placeholder password, or the wrong working directory.

> Security boundaries: This app intentionally simulates top-ups and accounts. It is **not a production fintech platform**; its authentication, abuse protection, observability, financial controls and secrets handling need additional hardening before use with actual financial assets or customer data. Caddy, SSH tunnels and disabling demo users are a starting point, not a certification.
