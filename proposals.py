"""
RoamDigi proposal email templates + renderer.

The three drafts are transcribed from RoamDigi_Partnership_Emails.pdf.
Placeholders in the templates:
    [Agency Name], [First Name], [Your Name], [Title],
    [X]  (commission number, Draft 1),
    [partner commission]  (phrase, Draft 2),
    [App Store link], [Google Play link]
"""
import re

# ---------------------------------------------------------------------------
# Draft templates (subject + plain-text body)
# ---------------------------------------------------------------------------

DRAFTS = {
    "draft1": {
        "id": "draft1",
        "name": "Draft 1 — New Revenue Stream",
        "blurb": "Leads with commission / bottom line. Best for margin-focused agencies.",
        "subject": "A new revenue stream for [Agency Name] — eSIM for your travelers",
        "body": """Hi [First Name],

I'm [Your Name] from RoamDigi. We provide travel eSIMs that give your clients instant mobile data the moment they land abroad — no roaming bills, no airport SIM queues, no physical chip to swap.

I'm reaching out because eSIMs have become one of the simplest add-ons a travel agency can offer. Every client traveling overseas needs connectivity, and most either overpay for roaming or land without a plan. By offering RoamDigi, [Agency Name] can solve that for them and earn on every sale.

What your clients get:
• Coverage across 190+ countries on a single eSIM profile
• Instant activation — they buy a plan, receive a QR code by email, scan it, and they're connected in minutes
• A cloud-based system that automatically connects them to the best available local carrier for stable, high-speed data
• Compatibility with most modern devices (Apple, Samsung, Google)

What's in it for [Agency Name]:
• Earn [X]% commission on every package your clients buy
• No tech setup — we can start with a simple referral link or co-branded code
• A smoother trip for your clients, which reflects well on you

You can see how it works here:
Website: https://www.roamdigi.com
Sign up / partner access: https://roamdigi.com/signup
[Mobile app line]

Would you be open to a quick 15-minute call this week? I'd be glad to share our partner rates and set you up with a sample plan to try yourself.

Best regards,
[Your Name]
[Title], RoamDigi
info@roamdigi.com | +1 585 282 6724
https://www.roamdigi.com""",
    },
    "draft2": {
        "id": "draft2",
        "name": "Draft 2 — Customer Experience",
        "blurb": "Leads with traveler pain point. Best for premium / relationship-driven agencies.",
        "subject": "Help your travelers stay connected abroad — effortlessly",
        "body": """Hi [First Name],

When your clients land in a new country, one of the first things they reach for is their phone — and too often that means surprise roaming charges or a frustrating hunt for a local SIM.

I'm [Your Name] from RoamDigi. We offer travel eSIMs that let travelers buy a data plan before they leave and connect the moment they land, with coverage across 190+ countries on one profile. Our system automatically connects them to the best local carrier, so they get stable, high-speed data without ever touching a physical SIM.

For an agency like [Agency Name], offering RoamDigi means your clients travel with one less thing to worry about — and they'll remember that you're the one who made their trip smoother. It's a small add-on that quietly raises the quality of every booking you handle, while opening a new revenue line for you through our [partner commission].

It's genuinely simple for them: buy a plan, get a QR code by email, scan it, done — works on most Apple, Samsung, and Google devices.

Take a look:
Website: https://www.roamdigi.com
Sign up: https://roamdigi.com/signup
[Mobile app line]

Could I send you a short overview and a sample plan to try yourself? Happy to set up a brief call if that's easier.

Warm regards,
[Your Name]
[Title], RoamDigi
info@roamdigi.com | +1 585 282 6724
https://www.roamdigi.com""",
    },
    "draft3": {
        "id": "draft3",
        "name": "Draft 3 — Short & Direct",
        "blurb": "Low-friction cold email that just asks for a call. Best for high-volume outreach.",
        "subject": "Quick idea for [Agency Name] — eSIM add-on for your travelers",
        "body": """Hi [First Name],

I'm [Your Name] from RoamDigi — we provide travel eSIMs that give travelers instant data in 190+ countries the moment they land, with no roaming fees and no physical SIM. They just scan a QR code emailed after purchase and they're online in minutes.

A lot of agencies now offer this as a paid add-on: clients get seamless connectivity, and the agency earns a commission on every sale. Setup takes minutes — no technical work on your side.

Have a look and you'll see how simple it is:
Website: https://www.roamdigi.com
Sign up: https://roamdigi.com/signup
[Mobile app line]

Worth a quick 15-minute call to see if it's a fit for [Agency Name]?

Thanks,
[Your Name]
[Title], RoamDigi
info@roamdigi.com | +1 585 282 6724""",
    },
}

DEFAULT_DRAFT = "draft2"


def draft_list():
    """Lightweight metadata for the UI selector."""
    return [
        {"id": d["id"], "name": d["name"], "blurb": d["blurb"], "subject": d["subject"]}
        for d in DRAFTS.values()
    ]


def clean_agency_name(raw):
    """Trim address junk that got concatenated into some agency names."""
    if not raw:
        return ""
    s = re.sub(r"\s+", " ", str(raw)).strip()
    # Cut at the first comma (addresses usually follow)
    s = s.split(",")[0].strip()
    # Drop a trailing hyphenated address/sector code like "G-E", "G-9", "F-7"
    # (requires the hyphen so real suffixes like "Ltd"/"Pvt" are kept).
    s = re.sub(r"\s+[A-Za-z]{1,3}-[A-Za-z0-9]{1,4}$", "", s).strip()
    return s or str(raw).strip()


def first_name_from(name, email):
    """
    Best-effort first name for the greeting.
    Most rows are brand/role mailboxes (info@, sales@, goglobal@), so this is
    intentionally conservative: it only returns a name for clear person-style
    locals like "abdul.manan@" or "ahmed_khan@". Otherwise it returns '' and the
    caller falls back to the configured greeting (e.g. "there").
    """
    if not email:
        return ""
    local = email.split("@")[0].lower()
    if any(c.isdigit() for c in local):
        return ""
    # Require a firstname<sep>lastname pattern — single-token locals are usually
    # the brand/role, not a person.
    parts = [p for p in re.split(r"[._-]", local) if p]
    if len(parts) >= 2 and parts[0].isalpha() and len(parts[0]) >= 2 and len(parts[1]) >= 2:
        generic = {
            "info", "sales", "contact", "admin", "office", "support", "booking",
            "bookings", "enquiry", "enquiries", "inquiry", "ceo", "hello", "no",
            "reservations", "travel", "tours", "marketing", "help", "team", "mail",
        }
        if parts[0] not in generic:
            return parts[0].capitalize()
    return ""


def _mobile_app_line(settings):
    appstore = (settings.get("app_store_url") or "").strip()
    play = (settings.get("play_store_url") or "").strip()
    if appstore and play:
        return f"Mobile app: {appstore} | {play}"
    if appstore:
        return f"Mobile app (iOS): {appstore}"
    if play:
        return f"Mobile app (Android): {play}"
    return ""  # nothing configured -> drop the line entirely


def render(draft_id, recipient, settings):
    """
    Returns dict: {subject, body, html, unresolved:[...]}
    `recipient` is a dict with at least agency_name, first_name, email.
    `settings` carries sender_name, title, commission, partner_commission,
    greeting_fallback, app_store_url, play_store_url.
    """
    draft = DRAFTS.get(draft_id) or DRAFTS[DEFAULT_DRAFT]

    agency = clean_agency_name(recipient.get("agency_name") or recipient.get("name") or "")
    first = (recipient.get("first_name") or "").strip()
    greeting = first or (settings.get("greeting_fallback") or "there").strip()

    repl = {
        "[Agency Name]": agency or "your agency",
        "[First Name]": greeting,
        "[Your Name]": (settings.get("sender_name") or "").strip(),
        "[Title]": (settings.get("title") or "").strip(),
        "[X]": str(settings.get("commission") or "").strip(),
        "[partner commission]": (settings.get("partner_commission") or "partner commission program").strip(),
        "[Mobile app line]": _mobile_app_line(settings),
    }

    def apply(text):
        for k, v in repl.items():
            text = text.replace(k, v)
        # If the mobile-app line was dropped, remove the now-empty line cleanly.
        text = re.sub(r"\n[ \t]*\n[ \t]*\n", "\n\n", text)
        return text

    subject = apply(draft["subject"])
    body = apply(draft["body"]).strip()

    # Detect any placeholder we failed to fill (e.g. empty sender name).
    unresolved = sorted(set(re.findall(r"\[[^\]]+\]", subject + "\n" + body)))

    return {
        "subject": subject,
        "body": body,
        "html": _to_html(body),
        "unresolved": unresolved,
    }


def _to_html(body):
    """Minimal, deliverability-friendly HTML version of the plain-text body."""
    import html as _html

    def linkify(line):
        line = _html.escape(line)
        line = re.sub(
            r"(https?://[^\s|]+)",
            r'<a href="\1" style="color:#1a73e8;text-decoration:none;">\1</a>',
            line,
        )
        line = re.sub(
            r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",
            r'<a href="mailto:\1" style="color:#1a73e8;text-decoration:none;">\1</a>',
            line,
        )
        return line

    paras = []
    for block in body.split("\n\n"):
        lines = [linkify(l) for l in block.split("\n")]
        paras.append("<br>".join(lines))
    inner = "".join(f'<p style="margin:0 0 14px 0;">{p}</p>' for p in paras)
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'line-height:1.5;color:#202124;max-width:640px;">' + inner + "</div>"
    )
