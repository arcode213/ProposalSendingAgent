# Deploying to Railway

This app needs a **long-lived process + persistent disk** (it sends emails from a
background thread and stores state in SQLite). Railway provides both: an always-on
service and a persistent volume. Do **not** deploy to Vercel or any serverless /
"free sleeping" tier — serverless kills the background thread and wipes the DB.

The repo includes everything Railway needs: `Dockerfile`, `.dockerignore`,
`railway.json` (single replica, `/healthz` health check), `app.py`, `templates/`,
`proposals.py`, `requirements.txt`, and the recipients spreadsheet.

> **Billing note:** Railway has **no free tier** (removed in 2023). New accounts get
> a small one-time trial credit; after that the **Hobby plan is ~$5/month minimum**.

`.env` and `outreach.db` are gitignored — secrets and local state are **not** pushed.

## 1. Push to GitHub
The `Dockerfile` and `railway.json` must be committed so Railway can build from them.
```bash
git add .
git commit -m "Add Railway deploy config"
git push
```

## 2. Create the project from your repo
1. Go to https://railway.app → **New Project → Deploy from GitHub repo**.
2. Pick this repo. Railway detects the `Dockerfile` and `railway.json` and builds.
3. If you pushed the parent monorepo, set the service's **Root Directory** to
   `ProposalSendingAgent` (Settings → Source).

## 3. Add a persistent volume
1. In the service, right-click → **Add Volume** (or Settings → **+ Volume**).
2. Set the **Mount path** to `/data`. (Size 1 GB is plenty.)

This keeps `outreach.db` (recipients, sent/failed status) across deploys & restarts.

## 4. Set the environment variables
Service → **Variables** → add these (same values as your local `.env`):

| Key                  | Value                                   |
|----------------------|-----------------------------------------|
| `DB_PATH`            | `/data/outreach.db`                     |
| `SECURE_COOKIES`     | `true`                                  |
| `BREVO_API_KEY`      | *your Brevo **API** key (`xkeysib-…`)*  |
| `SMTP_HOST`          | `smtp-relay.brevo.com`                  |
| `SMTP_PORT`          | `587`                                   |
| `SMTP_USER`          | `aa7a97001@smtp-brevo.com`              |
| `SMTP_PASS`          | *your Brevo SMTP key*                   |
| `FROM_EMAIL`         | `abdulrehman@roamdigi.com`              |
| `REPLY_TO`           | `abdulrehman@roamdigi.com`              |
| `SENDER_NAME`        | `RoamDigi`                              |
| `SENDER_TITLE`       | `Partner Program`                       |
| `ADMIN_USERNAME`     | `admin`                                 |
| `ADMIN_PASSWORD_HASH`| *the scrypt hash from your `.env`*      |
| `SECRET_KEY`         | *the long hex string from your `.env`*  |

Do **not** set `PORT` — Railway injects it and the app binds to it automatically.

> **Why `BREVO_API_KEY`?** Railway blocks outbound SMTP ports (25/465/587), so SMTP
> sends time out (`TimeoutError`). When `BREVO_API_KEY` is set, the app sends via
> Brevo's HTTP API over port 443 instead, which is never blocked. The `SMTP_*` vars
> are still used for local development (where SMTP works). The API key is **different**
> from the SMTP key: create it in Brevo → **SMTP & API → API Keys → Generate** — it
> starts with `xkeysib-` (the SMTP key starts with `xsmtpsib-`).

> Copy the **hash**, not the plaintext password, into `ADMIN_PASSWORD_HASH`.
> To change the password later:
> `python -c "from werkzeug.security import generate_password_hash as g; print(g('NEW_PASS'))"`

## 5. Deploy & open
Railway redeploys on each push and after the variable/volume changes above. Once
it's live, open **Settings → Networking → Generate Domain** to get a public URL.

First boot imports the 201 agencies into `/data/outreach.db`. Open the URL → you land
on the **login page**. Sign in (username `admin`, your password), then the dashboard
loads with the email account fields locked 🔒. Send a test email, then **Start auto-send**.

## Security notes
- **Login required** on every page and API route; sessions last 12 hours.
- **Password is stored hashed** (scrypt) — the plaintext is never in code, DB, or cookies.
- **Cookies** are HttpOnly + SameSite=Lax, and Secure (HTTPS-only) in production
  (the `SECURE_COOKIES` env var switches this on). SameSite=Lax blocks cross-site CSRF POSTs.
- **Brute-force throttle:** 5 failed logins from an IP → 5-minute lockout.
- **SMTP credentials & sender identity are env-only** and locked in the UI — they can't
  be read or changed from the browser (the password is never sent to the client).
- Rotate `SECRET_KEY` only when you intend to log everyone out.
- Keep `.env` and `outreach.db` out of git (already in `.gitignore`).

## Notes
- **Single replica only.** `railway.json` pins `numReplicas: 1`. Never scale up —
  multiple replicas would run multiple send threads and double-send. (More throughput
  isn't needed; sends are paced anyway.)
- **State persists** on the `/data` volume across restarts and deploys.
- **Local development** still uses `python app.py` (gunicorn is Linux-only; on Windows
  just run the Flask dev server via `run.bat`).
- Sender identity/credentials are locked from the UI and can only change via the
  Railway environment variables.
