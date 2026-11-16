# Putting Email Impact Studio online

Two routes are provided. Both give you HTTPS, a persistent database and password-reset emails. Neither route was built or run end to end where this project was written (that environment had no Docker daemon). What was verified: the Compose and Render files parse and pass `docker compose config`, and the app was run with the same environment settings the container uses (health check, HTTPS cookie and HSTS headers, proxy-aware login limits, reset links built from `BASE_URL`). Expect to fix small things on the first deploy.

## What you need first

- A domain name or subdomain you control (for Route A) or a Render account (Route B).
- An SMTP account for sending reset emails. Any provider works. Without one, reset links are only printed in the server log, which is fine for a private test and not for real users.

## Route A: your own server with Docker Compose (HTTPS handled by Caddy)

1. Get a small Linux server with Docker and Docker Compose installed. Point a DNS `A` record for your domain at the server's IP address, and open ports 80 and 443.
2. Copy this project to the server (`git clone`, or unzip it).
3. `cp .env.example .env`, then edit `.env`: set `DOMAIN`, `BASE_URL` (for example `https://studio.example.com`) and the `SMTP_*` values.
4. `docker compose up -d --build`
5. Open your address. Caddy requests and renews the certificate on its own. Create your account on the registration page.
6. Close public sign-ups: set `ALLOW_REGISTRATION=0` in `.env`, then `docker compose up -d`. Add people later with
   `docker compose exec app python app/server.py create-user person@example.com "Full Name"`.

## Route B: Render (no server to manage)

1. Put this project in a GitHub repository.
2. In Render choose **New > Blueprint**, select the repository, and confirm. Render reads `render.yaml`.
3. When asked, enter `BASE_URL` (the address Render gives the service, for example `https://email-impact-studio.onrender.com`; you can add a custom domain later and update this value) and the `SMTP_*` values.
4. The blueprint attaches a 1 GB persistent disk at `/data`. Without it, accounts and history are lost on every deploy, and Render charges for disks, so the blueprint uses a paid instance type.
5. Register your account, then set `ALLOW_REGISTRATION` to `0` in the Render dashboard to close sign-ups.

## Settings reference

| Variable | Meaning |
|---|---|
| `BASE_URL` | Public address used in reset emails. Always set it. It is never taken from the request. |
| `SECURE_COOKIES` | `1` when served over HTTPS. Adds the `Secure` cookie flag and an HSTS header. Already set in both routes. |
| `TRUST_PROXY` | `1` when behind a proxy you control, so login limits use the real client address. Already set in both routes. |
| `ALLOW_REGISTRATION` | `0` closes public sign-up. Existing users can still sign in and reset passwords. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY` | Outgoing mail. `SMTP_SECURITY` is `starttls` (port 587), `ssl` (port 465) or `none`. |
| `DB_PATH` | Database file. The container uses `/data/app.db`. |

## Operating it

- **Locked-out user without working email:** `docker compose exec app python app/server.py reset-link person@example.com` prints a one-time link to send them yourself. Set `BASE_URL` in the environment so the link has your domain.
- **Backup** (copy the result off the server regularly):
  `docker compose exec app python -c "import sqlite3; s=sqlite3.connect('/data/app.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d)"`
  then `docker compose cp app:/data/backup.db ./backup.db`.
- **Update:** pull the new code, then `docker compose up -d --build`. The database volume is kept.
- **Logs:** `docker compose logs -f app`. Avoid sharing them: with SMTP unset they contain reset links.

## Limits

- One instance only. SQLite is not meant for several servers writing at once, and the built-in web server handles modest traffic.
- No email verification at sign-up and no two-factor sign-in. If sign-ups stay open to the public, add both first.
- The model describes one past campaign. See the README for what the results can and cannot support.
