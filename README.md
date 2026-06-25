# RoamDigi — Proposal Sending Agent

A small web app that sends the RoamDigi partnership proposal to your list of
Pakistan travel agencies **one by one**, from your own Gmail account, with a
visual dashboard: live preview, per-agency status, paced (spam-safe) sending,
pause/resume/stop, and a manual one-at-a-time mode.

It reads the verified agency list already in this folder:

- `pk_travel_agencies_VALID_EMAILS_v2.xlsx` — the 201 verified agencies

There are **no preset proposals**. You **compose the proposal at run time** in the
Compose tab — paste your email data (subject + body) and the agent formats it into
a clean, professional email, adding a **formal greeting that uses the agency
name** automatically. You can also **add / edit / delete agency records** from the
dashboard, and **send the proposal to a single recipient on demand** (the ✉
button on any row).

---

## 1. One-time setup — create a Gmail App Password

Gmail will not let an app log in with your normal password. You need a 16-character
**App Password** (free, takes 2 minutes):

1. Turn on **2-Step Verification** for your Google account:
   <https://myaccount.google.com/security>
2. Go to **App Passwords**: <https://myaccount.google.com/apppasswords>
3. Type a name like `RoamDigi Sender` and click **Create**.
4. Copy the 16-character code it shows (e.g. `abcd efgh ijkl mnop`).

You'll paste that into the app's **Gmail App Password** field (spaces are fine).

> Sending from `info@roamdigi.com` instead? Use that mailbox's own SMTP host /
> credentials in the settings panel — the host field accepts any SMTP server.

---

## 2. Run the app

From the **`ProposalSendingAgent`** folder:

```powershell
# easiest — double-click run.bat, OR from a terminal:
pip install -r requirements.txt
python app.py
```

Then open **<http://127.0.0.1:5000>** in your browser.

On first run it imports all 201 agencies into a local database (`outreach.db`).

---

## 3. Send

1. **Left panel → Gmail account:** your Gmail address + the App Password.
2. **Your details:** your name, title, commission %, and (optional) app-store links.
   These fill the `[Your Name]`, `[Title]`, `[X]%`, etc. placeholders.
3. Open the **Compose proposal** tab, paste your subject + body (you may use
   placeholders like `[Agency Name]`, `[Your Name]`, `[X]`), and click **Save
   proposal**. *Do not type a greeting* — the agent adds a formal one with the
   agency name (e.g. "Dear Sir/Madam at Skyline Travels,") and formats the body.
4. Click **Save settings**. The panel shows **✓ Ready to send** when complete.
5. Click **Send test email** to send one to yourself first and check how it looks.
6. **Recipients tab:** review the list. Click any row to preview its exact email.
   Use **+ Add agency** to create a record, the **✎** button to edit every field
   (name, city, email, contact, business type, reliability, notes), **🗑** to
   delete, and **✉** to send the proposal to that one agency immediately. You can
   still click a cell to quick-edit inline, and toggle **On** to include/exclude.
7. Press **▶ Start auto-send.** Emails go out one at a time with a gap between
   each (default 45s) up to the daily cap (default 120/day). Use **Pause**,
   **Resume**, **Stop**, **Send next (manual)**, or the per-row **✉** at any time.

Progress, counts, and an activity log update live. Status is saved to
`outreach.db`, so **no agency is ever emailed twice** — you can close the app and
resume later. Failed sends can be retried with **Reset failed → pending**.

---

## Sending limits & deliverability (read this)

- A normal Gmail account can send roughly **500 emails/day**. The default cap is
  **120/day** and a **45-second gap** to stay well clear of spam filters. At that
  pace the full list of ~201 takes ~2 days.
- Cold outreach has real spam risk. Keep the gap, send a test to yourself first,
  and consider warming up (start with a smaller daily cap).
- Replies go to the **Reply-To** address (`info@roamdigi.com` by default), so the
  RoamDigi inbox receives responses even though it's sent from your Gmail.

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask server, database, SMTP sender, API |
| `proposals.py` | The run-time proposal renderer: formal agency greeting, professional formatting, and placeholder substitution |
| `templates/index.html` | The web dashboard |
| `outreach.db` | Auto-created local state (recipients, status, settings, log) |
| `run.bat` | Double-click launcher (Windows) |

To start over from scratch, delete `outreach.db` and run again.
