#!/usr/bin/env python3
"""
Granola Daily Meeting Digest v2
Pulls yesterday's meeting notes from Granola, runs them through Claude Opus
as a deal desk analyst / sales coach, and emails a formatted HTML digest.
"""

import os
import sys
import json
import smtplib
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── Configuration (all from environment variables) ──────────────────────────
GRANOLA_API_KEY = os.environ.get("GRANOLA_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
EMAIL_TO = os.environ.get("EMAIL_TO")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")

GRANOLA_BASE = "https://public-api.granola.ai/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-opus-4-6"
CT = ZoneInfo("America/Chicago")


def check_config():
    """Validate all required environment variables are set."""
    required = {
        "GRANOLA_API_KEY": GRANOLA_API_KEY,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "EMAIL_TO": EMAIL_TO,
        "SMTP_USER": SMTP_USER,
        "SMTP_PASS": SMTP_PASS,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        sys.exit(1)


def granola_request(path):
    """Make an authenticated GET request to the Granola API."""
    url = f"{GRANOLA_BASE}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {GRANOLA_API_KEY}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"Granola API error {e.code}: {body}")
        return None


def fetch_yesterday_notes():
    """Fetch all meeting notes from yesterday (full day, Central Time)."""
    today_start = datetime.now(CT).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = (today_start - timedelta(days=1)).astimezone(timezone.utc)
    yesterday = yesterday_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    notes = []
    cursor = None

    while True:
        path = f"/notes?created_after={yesterday}"
        if cursor:
            path += f"&cursor={cursor}"

        data = granola_request(path)
        if not data or "notes" not in data:
            break

        notes.extend(data["notes"])

        if data.get("hasMore") and data.get("cursor"):
            cursor = data["cursor"]
        else:
            break

    return notes


def fetch_note_with_transcript(note_id):
    """Fetch a single note with its full transcript."""
    return granola_request(f"/notes/{note_id}?include=transcript")


# ── The Prompt ───────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a fractional Deal Desk Analyst and Sales Coach embedded with a Regional Sales Director at a B2B HCM/payroll software company (Paylocity). You review daily meeting transcripts and produce a morning intelligence briefing — not an administrative summary.

Your output must be clean, inline HTML. Do NOT use Markdown syntax. Do NOT wrap output in code blocks or backticks. Use only HTML tags: <h2>, <h3>, <p>, <ul>, <li>, <strong>, <em>, <span>, <table>, <tr>, <td>, <th>.

Strip out all pleasantries, travel chatter, small talk, and filler. Focus exclusively on pipeline movement, deal mechanics, coaching opportunities, and strategic decisions.

For each meeting, output this exact structure:

<meeting>
<h3>[Meeting Title]</h3>
<p class="meta"><strong>Attendees:</strong> [names and roles where identifiable]</p>

<p class="deal-status"><strong>Deal Status / Situation Read:</strong> [2-3 sentences. Read between the lines. Where does this deal ACTUALLY stand? Is the prospect buying or stalling? Did the rep create urgency or let it slip? Is there a hard next step with a date, or a soft "we'll circle back"? Be brutally honest.]</p>

<p><strong>Key Decisions:</strong></p>
<ul>[Only decisions that were actually locked in. If none, omit this section entirely.]</ul>

<p><strong>Coaching Notes:</strong></p>
<ul>[1-2 specific, actionable observations. Did the rep miss a buying signal? Give away leverage too early? Fail to isolate an objection? Let a competitor mention slide without probing? If the call was run well, say so in one line and move on. Do NOT fabricate coaching points — only flag what genuinely happened.]</ul>

<p><strong>Next Steps:</strong></p>
<ul>[Single merged list. Every item has an <strong>owner</strong> and a <strong>deadline</strong>. If no deadline was stated, write "no date set — needs one." AE-level tasks and director-level tasks both go here but tag director items with 🔴.]</ul>

<p><strong>Market Intel:</strong></p>
<ul>[Competitor mentions, pricing objections, feature requests, or industry intel worth logging. If none, omit this section.]</ul>
</meeting>

After all meetings, output exactly this closing section:

<h2>🔴 Director Priorities — Today</h2>
<p>Maximum 3 items. These are ONLY things that require the Sales Director's personal intervention, approval, or decision today. Everything else belongs to the AEs. If fewer than 3 warrant director action, list fewer.</p>
<ol>[Each item: what to do, why it matters, and the specific rep/deal involved.]</ol>

<h2>📊 Pipeline Pulse</h2>
<p>[3-4 sentences. Across all of yesterday's meetings, what is the overall trajectory? Any deals accelerating? Any slipping? Any patterns across reps — e.g., multiple reps struggling with the same objection, or a competitor showing up repeatedly?]</p>"""


def summarize_with_claude(meetings_text):
    """Send meeting transcripts to Claude Opus for analysis."""
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 16384,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"Analyze yesterday's meeting transcripts and produce my morning briefing:\n\n{meetings_text}"
            }
        ],
    }).encode()

    req = urllib.request.Request(ANTHROPIC_URL, data=payload, headers={
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    })

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            return "".join(
                block["text"] for block in data.get("content", [])
                if block.get("type") == "text"
            )
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"Anthropic API error {e.code}: {body}")
        return None


# ── Email Template ───────────────────────────────────────────────────────────

def wrap_html(body_content, digest_date):
    """Wrap Claude's HTML output in a polished email template."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background-color:#111111; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#111111;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="680" cellpadding="0" cellspacing="0" style="max-width:680px; width:100%;">

<!-- Header -->
<tr><td style="background:linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius:12px 12px 0 0; padding:32px 36px 24px;">
  <h1 style="margin:0 0 4px; font-size:22px; font-weight:700; color:#e0e0e0; letter-spacing:-0.3px;">Morning Briefing</h1>
  <p style="margin:0; font-size:14px; color:#888888;">{digest_date}</p>
</td></tr>

<!-- Body -->
<tr><td style="background-color:#1a1a1a; padding:28px 36px 36px; border-radius:0 0 12px 12px;">
  <style>
    h2 {{
      font-size:18px; font-weight:700; color:#e0e0e0;
      margin:36px 0 12px; padding:16px 0 8px;
      border-top:1px solid #333333;
    }}
    h2:first-child {{ border-top:none; margin-top:0; padding-top:0; }}
    h3 {{
      font-size:16px; font-weight:700; color:#c8d6e5;
      margin:28px 0 8px; padding:0;
    }}
    p {{ font-size:14px; line-height:1.65; color:#cccccc; margin:8px 0; }}
    p.meta {{ font-size:13px; color:#888888; margin:4px 0 12px; }}
    p.deal-status {{
      background-color:#1e2a3a; border-left:3px solid #4a9eff;
      padding:12px 16px; border-radius:0 6px 6px 0; margin:12px 0;
      font-size:14px; line-height:1.6; color:#b8cce0;
    }}
    ul {{ margin:6px 0 12px 0; padding-left:20px; }}
    li {{
      font-size:14px; line-height:1.6; color:#cccccc;
      margin-bottom:6px; padding-left:4px;
    }}
    ol {{ margin:8px 0 12px 0; padding-left:20px; }}
    ol li {{
      font-size:14px; line-height:1.6; color:#cccccc;
      margin-bottom:10px; padding-left:4px;
    }}
    strong {{ color:#e0e0e0; }}
    em {{ color:#aaaaaa; font-style:italic; }}
    table.intel {{ width:100%; border-collapse:collapse; margin:8px 0; }}
    table.intel th {{
      text-align:left; font-size:12px; font-weight:600;
      color:#888888; text-transform:uppercase; letter-spacing:0.5px;
      padding:6px 12px; border-bottom:1px solid #333333;
    }}
    table.intel td {{
      font-size:14px; color:#cccccc; padding:8px 12px;
      border-bottom:1px solid #222222;
    }}
  </style>
  {body_content}
</td></tr>

<!-- Footer -->
<tr><td style="padding:20px 36px 8px; text-align:center;">
  <p style="margin:0; font-size:12px; color:#555555;">
    Generated by Claude Opus &middot; Granola &rarr; Anthropic API &middot; GitHub Actions
  </p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def strip_plaintext(html):
    """Rough plaintext fallback: strip tags for non-HTML email clients."""
    import re
    text = re.sub(r'<br\s*/?>', '\n', html)
    text = re.sub(r'</(p|li|h[1-6]|tr|div)>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def send_email(subject, html_body):
    """Send the digest via SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO

    # Plaintext fallback
    msg.attach(MIMEText(strip_plaintext(html_body), "plain"))
    # HTML version (primary)
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())

    print(f"Digest emailed to {EMAIL_TO}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    check_config()

    # Step 1: Fetch yesterday's notes
    print("Fetching yesterday's meeting notes from Granola...")
    notes = fetch_yesterday_notes()

    if not notes:
        print("No meetings found from yesterday. Skipping digest.")
        return

    print(f"Found {len(notes)} meeting(s). Fetching transcripts...")

    # Step 2: Fetch transcripts
    meetings_text_parts = []
    for note in notes:
        note_id = note.get("id", "unknown")
        title = note.get("title", "Untitled Meeting")
        print(f"  → {title}")

        full_note = fetch_note_with_transcript(note_id)
        if not full_note:
            meetings_text_parts.append(f"MEETING: {title}\n[Transcript unavailable]\n")
            continue

        transcript_lines = []
        for entry in full_note.get("transcript", []):
            speaker = (entry.get("speaker", {}).get("name")
                       or entry.get("speaker", {}).get("source", "Unknown"))
            text = entry.get("text", "")
            transcript_lines.append(f"{speaker}: {text}")

        transcript_text = "\n".join(transcript_lines) if transcript_lines else "[No transcript]"

        granola_summary = full_note.get("summary", "")
        section = f"MEETING: {title}\n"
        if granola_summary:
            section += f"GRANOLA SUMMARY:\n{granola_summary}\n\n"
        section += f"TRANSCRIPT:\n{transcript_text}\n"
        meetings_text_parts.append(section)

    meetings_text = "\n" + ("=" * 60) + "\n\n".join(meetings_text_parts)

    # Step 3: Analyze with Claude Opus
    print("Generating briefing with Claude Opus...")
    digest_html = summarize_with_claude(meetings_text)

    if not digest_html:
        print("ERROR: Failed to generate briefing.")
        sys.exit(1)

    # Step 4: Wrap in email template and send
    now_ct = datetime.now(CT)
    digest_date = now_ct.strftime("%A, %B %-d, %Y")
    subject = f"Morning Briefing — {digest_date}"

    full_html = wrap_html(digest_html, digest_date)

    print("Sending email...")
    send_email(subject, full_html)
    print("Done!")


if __name__ == "__main__":
    main()
