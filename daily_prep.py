#!/usr/bin/env python3
"""
Granola Daily Prep Brief
Reads the last 14 days of morning digest analyses from digests/, sends them
to Claude Opus to identify tomorrow's meetings and generate prep cards.

Runs at 4pm CT weekdays. On Fridays, preps for Monday.
"""

import os
import re
import sys
import json
import smtplib
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
EMAIL_TO = os.environ.get("EMAIL_TO")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-opus-4-6"
CT = ZoneInfo("America/Chicago")

DIGEST_DIR = Path(__file__).parent / "digests"
LOOKBACK_DAYS = 14


def check_config():
    required = {
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "EMAIL_TO": EMAIL_TO,
        "SMTP_USER": SMTP_USER,
        "SMTP_PASS": SMTP_PASS,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        sys.exit(1)


# ── Load Digest Archive ─────────────────────────────────────────────────────

def load_recent_digests():
    """Load the last 14 days of saved digest files."""
    if not DIGEST_DIR.exists():
        return []

    now_ct = datetime.now(CT)
    cutoff = now_ct - timedelta(days=LOOKBACK_DAYS)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    digests = []
    for filepath in sorted(DIGEST_DIR.glob("*.json")):
        date_str = filepath.stem  # e.g., "2026-05-12"
        if date_str >= cutoff_str:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                digests.append(data)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: Could not read {filepath}: {e}")

    return digests


# ── Determine Prep Target ────────────────────────────────────────────────────

def get_prep_target():
    """Return the date we're prepping for. Mon-Thu = tomorrow. Fri = Monday."""
    now_ct = datetime.now(CT)
    weekday = now_ct.weekday()  # 0=Mon, 4=Fri

    if weekday == 4:  # Friday → prep for Monday
        target = now_ct + timedelta(days=3)
    else:  # Mon-Thu → prep for tomorrow
        target = now_ct + timedelta(days=1)

    return target


# ── The Prep Prompt ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = r"""You are a fractional Deal Desk Analyst preparing an afternoon prep brief for a Regional Sales Director at Paylocity (B2B HCM/payroll). You are given the last 14 days of analyzed morning digest summaries. Your job is to identify everything scheduled for the PREP TARGET DATE and generate a prep card for each one.

<critical_context>
This system does NOT have calendar access. You are mining meeting transcripts for references to future meetings, calls, demos, and events. This means:
- You must resolve relative dates ("tomorrow," "next Wednesday," "the 18th," "this Friday") against the date each digest was recorded.
- Some meetings may have been referenced with ambiguous timing ("in a couple weeks," "sometime this month"). Surface these as "POSSIBLE — date uncertain" rather than guessing.
- If a meeting was scheduled outside of a recorded call (via email, Slack, calendar invite only), it will NOT appear in this data. This brief is HIGH-PROBABILITY PREP, not a complete calendar.
</critical_context>

<output_format>
Output clean, inline-styled HTML. No Markdown. No <style> blocks. All styles inline.
Use only: <h2>, <h3>, <p>, <ul>, <li>, <ol>, <strong>, <em>, <span>, <br>.
</output_format>

<styling_reference>
Prep card title:
  <h3 style="font-size:16px; font-weight:700; color:#c8d6e5; margin:28px 0 6px; padding:0;">

Metadata line:
  <p style="font-size:13px; color:#888888; margin:4px 0 12px;">

Context block (green sidebar):
  <p style="background-color:#1a2e1e; border-left:3px solid #4aff6e; padding:12px 16px; border-radius:0 6px 6px 0; margin:12px 0; font-size:14px; line-height:1.6; color:#b8e0c0;">

Confidence tag (high):
  <span style="background-color:#1a3a1e; color:#6aff8e; padding:2px 8px; border-radius:3px; font-size:12px; font-weight:600;">HIGH CONFIDENCE</span>

Confidence tag (medium):
  <span style="background-color:#3a3a1e; color:#ffe06a; padding:2px 8px; border-radius:3px; font-size:12px; font-weight:600;">MEDIUM — date inferred</span>

Confidence tag (low):
  <span style="background-color:#3a1e1e; color:#ff6a6a; padding:2px 8px; border-radius:3px; font-size:12px; font-weight:600;">POSSIBLE — date uncertain</span>

Section labels, bullet lists, bold text, body text:
  Same styles as the morning briefing (use inline styles on every tag).

Bullet lists:
  <ul style="margin:4px 0 14px 0; padding-left:20px;">
  <li style="font-size:14px; line-height:1.6; color:#cccccc; margin-bottom:5px;">

Section headers:
  <h2 style="font-size:18px; font-weight:700; color:#e0e0e0; margin:36px 0 10px; padding:16px 0 8px; border-top:1px solid #333333;">

Body text:
  <p style="font-size:14px; line-height:1.65; color:#cccccc; margin:8px 0;">

Bold:
  <strong style="color:#e0e0e0;">
</styling_reference>

<prep_card_structure>
For each meeting/event identified for the prep target date:

1. MEETING TITLE — what the event is, with confidence tag on the same line.

2. METADATA — who is involved (AE, prospect contacts, partners), time if stated, location if stated.

3. CONTEXT RECAP (green sidebar) — 80-120 words. Synthesize everything discussed about this prospect/partner/event across ALL digests in the archive. What happened in prior meetings? What was discussed about them in internal 1:1s? What's the full story arc? This is the section that makes the AE feel like you were on every call.

4. OPEN COMMITMENTS — Bullet list of things you or the AE said you'd do that should be complete before this meeting. Pull these from Next Steps sections across all digests. Flag anything that was committed but never confirmed as done with ⚠️.

5. PROSPECT INTELLIGENCE — Bullet list. Objections raised, competitors mentioned, pricing sensitivities, decision criteria, key contacts and roles, decision timeline. Only include what was actually stated in transcripts.

6. STRATEGIC ANGLE — 2-3 sentences. What should the conversation focus on? What should be probed? What would make the prospect feel heard? What's the single most important outcome to achieve in this meeting?

7. PRE-MEETING CHECKLIST — Bullet list of specific items to verify are done. Materials to send, slides to review, pricing to confirm, people to loop in. Tag who owns each item.
</prep_card_structure>

<closing_section>
After all prep cards, output:

<h2>⚠️ Unscheduled Items Approaching</h2>
<p>List any meetings, events, or follow-ups from the last 14 days that were referenced with vague timing ("in a couple weeks," "sometime this month," "after the conference") and may be approaching but have no confirmed date. These need scheduling attention.</p>
<ul>[Items with context on which AE owns them]</ul>

If no meetings are identified for the prep target date AND no unscheduled items are approaching, output a brief note: "No meetings identified for [date] in the last 14 days of transcripts. Check your calendar directly — this brief only captures meetings discussed on recorded calls."
</closing_section>"""


# ── Claude API ───────────────────────────────────────────────────────────────

def generate_prep(digests_text, today_str, target_str, target_day_name):
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 16384,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Today is {today_str} ({target_day_name} prep).\n"
                    f"The PREP TARGET DATE is {target_str}.\n\n"
                    f"Below are the last 14 days of morning digest analyses. "
                    f"Identify every meeting, call, demo, event, or follow-up "
                    f"scheduled for {target_str} and generate prep cards.\n\n"
                    f"{digests_text}"
                )
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


# ── Email ────────────────────────────────────────────────────────────────────

def wrap_html(body_content, prep_date, target_date):
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background-color:#111111; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#111111;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="680" cellpadding="0" cellspacing="0" style="max-width:680px; width:100%;">

<tr><td style="background:linear-gradient(135deg, #1e2a1a 0%, #162e1e 100%); border-radius:12px 12px 0 0; padding:32px 36px 24px;">
  <h1 style="margin:0 0 4px; font-size:22px; font-weight:700; color:#e0e0e0; letter-spacing:-0.3px; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">Prep Brief</h1>
  <p style="margin:0; font-size:14px; color:#888888; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">Prepping for {target_date}</p>
  <p style="margin:4px 0 0; font-size:12px; color:#666666; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">Based on 14 days of meeting transcripts &middot; Not a complete calendar</p>
</td></tr>

<tr><td style="background-color:#1a1a1a; padding:28px 36px 36px; border-radius:0 0 12px 12px; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  {body_content}
</td></tr>

<tr><td style="padding:20px 36px 8px; text-align:center;">
  <p style="margin:0; font-size:12px; color:#555555; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
    Generated by Claude Opus &middot; Digest Archive &rarr; Anthropic API &middot; GitHub Actions
  </p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def strip_plaintext(html):
    text = re.sub(r'<br\s*/?>', '\n', html)
    text = re.sub(r'</(p|li|h[1-6]|tr|div)>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO

    msg.attach(MIMEText(strip_plaintext(html_body), "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())

    print(f"Prep brief emailed to {EMAIL_TO}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    check_config()

    # Load digest archive
    print(f"Loading digests from {DIGEST_DIR}...")
    digests = load_recent_digests()

    if not digests:
        print("No digest files found in the last 14 days. Skipping prep brief.")
        print("(The morning digest needs to run first to populate the archive.)")
        return

    print(f"Loaded {len(digests)} digest(s) from the last {LOOKBACK_DAYS} days.")

    # Determine prep target
    target = get_prep_target()
    now_ct = datetime.now(CT)

    today_str = now_ct.strftime("%A, %B %-d, %Y")
    target_str = target.strftime("%A, %B %-d, %Y")
    target_day_name = target.strftime("%A")

    print(f"Today: {today_str}")
    print(f"Prepping for: {target_str}")

    # Build context from digests
    digests_text_parts = []
    for d in digests:
        date = d.get("date", "unknown")
        meetings = d.get("meetings", [])
        analysis = d.get("analysis_html", "")

        # Strip HTML tags to get clean text for the prompt (saves tokens)
        clean_analysis = re.sub(r'<[^>]+>', ' ', analysis)
        clean_analysis = re.sub(r'\s+', ' ', clean_analysis).strip()

        part = f"=== DIGEST FROM {date} ===\n"
        part += f"Meetings recorded: {', '.join(meetings)}\n\n"
        part += f"{clean_analysis}\n"
        digests_text_parts.append(part)

    digests_text = "\n\n".join(digests_text_parts)

    # Generate prep brief
    print("Generating prep brief with Claude Opus...")
    prep_html = generate_prep(digests_text, today_str, target_str, target_day_name)

    if not prep_html:
        print("ERROR: Failed to generate prep brief.")
        sys.exit(1)

    # Email
    subject = f"Prep Brief — {target_str}"
    full_html = wrap_html(prep_html, today_str, target_str)

    print("Sending email...")
    send_email(subject, full_html)
    print("Done!")


if __name__ == "__main__":
    main()
