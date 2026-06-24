# Deploying to Render

This app needs a **long-lived process + persistent disk** (it sends emails from a
background thread and stores state in SQLite). Render's Starter plan provides both.
Do **not** deploy to Vercel — serverless kills the background thread and wipes the DB.

## 1. Push to GitHub
The repo includes everything Render needs: `render.yaml`, `requirements.txt`,
`app.py`, `templates/`, `proposals.py`, and the recipients spreadsheet.

`.env` and `outreach.db` are gitignored — secrets and local state are **not** pushed.

```bash
git add .
git commit -m "Deploy RoamDigi proposal agent to Render"
git push
```

## 2. Create the service on Render
1. Go to https://dashboard.render.com → **New → Blueprint**.
2. Select this GitHub repo. Render reads `render.yaml` and proposes one web service
   `roamdigi-proposal-agent` with a 1 GB disk mounted at `/var/data`.
3. Click **Apply**.

> **Root directory:** this repo's root **is** the app (`render.yaml`, `app.py`, etc.
> are at the top level). If instead you push the parent monorepo, set the service's
> **Root Directory** to `ProposalSendingAgent` in Render.

## 3. Set the secrets (Environment Variables)
The blueprint marks these as `sync: false`, so Render asks you to fill them in.
Use the same values from your local `.env`:

| Key                  | Value                                   |
|----------------------|-----------------------------------------|
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

(`DB_PATH`, `PYTHON_VERSION`, and `RENDER` are already set by the blueprint — leave them.)

> Copy the **hash**, not the plaintext password, into `ADMIN_PASSWORD_HASH`.
> To change the password later:
> `python -c "from werkzeug.security import generate_password_hash as g; print(g('NEW_PASS'))"`

## 4. Deploy & open
Render builds, then starts:
`gunicorn app:app --workers 1 --threads 8 --timeout 120` (health check: `/healthz`).

On first boot it imports the 201 agencies into `/var/data/outreach.db`. Open the
service URL → you land on the **login page**. Sign in (username `admin`, your
password), then the dashboard loads with the email account fields locked 🔒.
Send a test email, then **Start auto-send**.

## Security notes
- **Login required** on every page and API route; sessions last 12 hours.
- **Password is stored hashed** (scrypt) — the plaintext is never in code, DB, or cookies.
- **Cookies** are HttpOnly + SameSite=Lax, and Secure (HTTPS-only) in production
  (the `RENDER` env var switches this on). SameSite=Lax blocks cross-site CSRF POSTs.
- **Brute-force throttle:** 5 failed logins from an IP → 5-minute lockout.
- **SMTP credentials & sender identity are env-only** and locked in the UI — they can't
  be read or changed from the browser (the password is never sent to the client).
- Rotate `SECRET_KEY` only when you intend to log everyone out.
- Keep `.env` and `outreach.db` out of git (already in `.gitignore`).

## Notes
- **One worker only.** Never raise `--workers` — multiple workers would run multiple
  send threads and double-send. (More throughput isn't needed; sends are paced anyway.)
- **State persists** on the `/var/data` disk across restarts and deploys.
- **Local development** still uses `python app.py` (gunicorn is Linux-only; on Windows
  just run the Flask dev server via `run.bat`).
- Sender identity/credentials are locked from the UI and can only change via the
  Render environment variables.
