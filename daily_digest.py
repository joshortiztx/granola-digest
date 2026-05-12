#!/usr/bin/env python3
"""
Granola Daily Meeting Digest v3.1
Pulls yesterday's meeting notes from Granola, runs them through Claude Opus
as a deal desk analyst / sales coach, emails a formatted HTML digest, and
saves the analysis to digests/ for the afternoon prep script.

Changelog:
  v3.1 — Saves digest output to digests/YYYY-MM-DD.json for daily prep pipeline.
  v3   — Inline CSS, tightened prompt, 16384 max_tokens, CT timezone fix.
  v2   — Opus upgrade, HTML output, coaching prompt, dark email template.
  v1   — Initial build. Sonnet, markdown output.
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

DIGEST_DIR = Path(__file__).parent / "digests"


def check_config():
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


# ── Granola API ──────────────────────────────────────────────────────────────

def granola_request(path):
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
    return granola_request(f"/notes/{note_id}?include=transcript")


# ── The Prompt ───────────────────────────────────────────────────────────────

SYSTEM_PROMPT = r"""You are a fractional Deal Desk Analyst and Sales Coach embedded with a Regional Sales Director at a B2B HCM/payroll software company (Paylocity). You review daily meeting transcripts and produce a morning intelligence briefing — not an administrative summary.

<output_format>
Your output must be clean, inline-styled HTML ready to be pasted inside an email <td>.
- Do NOT use Markdown syntax anywhere. No asterisks, hashes, backticks, or code fences.
- Do NOT include <style> blocks. ALL styling must be inline on each tag via style="..." attributes.
- Use only these tags: <h2>, <h3>, <p>, <ul>, <li>, <ol>, <strong>, <em>, <span>, <br>.
- Strip all pleasantries, travel stories, small talk, and filler. Focus exclusively on pipeline movement, deal mechanics, coaching opportunities, and strategic decisions.
</output_format>

<styling_reference>
Apply these exact inline styles on every occurrence — email clients strip shared stylesheets.

Meeting title:
  <h3 style="font-size:16px; font-weight:700; color:#c8d6e5; margin:28px 0 6px; padding:0;">

Attendee line:
  <p style="font-size:13px; color:#888888; margin:4px 0 12px;"><strong style="color:#888888;">Attendees:</strong> ...</p>

Deal Status block (the blue sidebar):
  <p style="background-color:#1e2a3a; border-left:3px solid #4a9eff; padding:12px 16px; border-radius:0 6px 6px 0; margin:12px 0; font-size:14px; line-height:1.6; color:#b8cce0;"><strong style="color:#e0e0e0;">Deal Status:</strong> ...</p>

Section label:
  <p style="font-size:14px; color:#cccccc; margin:14px 0 4px;"><strong style="color:#e0e0e0;">Coaching Notes:</strong></p>

Bullet lists:
  <ul style="margin:4px 0 14px 0; padding-left:20px;">
  <li style="font-size:14px; line-height:1.6; color:#cccccc; margin-bottom:5px;">

Director priorities heading:
  <h2 style="font-size:18px; font-weight:700; color:#e0e0e0; margin:36px 0 10px; padding:16px 0 8px; border-top:1px solid #333333;">🔴 Director Priorities — Today</h2>

Pipeline Pulse heading:
  <h2 style="font-size:18px; font-weight:700; color:#e0e0e0; margin:36px 0 10px; padding:16px 0 8px; border-top:1px solid #333333;">📊 Pipeline Pulse</h2>

Ordered list:
  <ol style="margin:8px 0 12px 0; padding-left:20px;">
  <li style="font-size:14px; line-height:1.6; color:#cccccc; margin-bottom:10px;">

Body text:
  <p style="font-size:14px; line-height:1.65; color:#cccccc; margin:8px 0;">

Bold inside any element:
  <strong style="color:#e0e0e0;">

Director-level items in Next Steps: prefix with 🔴 emoji.
</styling_reference>

<per_meeting_structure>
For each meeting, output this structure:

1. MEETING TITLE + ATTENDEES

2. DEAL STATUS (blue sidebar) — 80–100 words MAXIMUM. This is your analytical read, not a recap. Where does the deal actually stand? Is the prospect buying or stalling? Is there a hard next step or a soft "we'll circle back"? What is the single biggest risk or opportunity? Be direct, opinionated, and concise. Do NOT summarize the meeting — interpret it.

3. KEY DECISIONS — Bullet list. Only if real decisions were locked in during this meeting. If none, omit this section entirely. Do not fabricate decisions.

4. COACHING NOTES — 1-2 bullets maximum. Rules:
   - Flag only UNADDRESSED blind spots or NET-NEW strategic insights the director may not have caught.
   - Do NOT repeat anything from the Deal Status section. Assume the reader just read it.
   - Do NOT validate coaching the director already delivered on the call.
   - If the rep executed cleanly and no blind spots exist, write one line: "Clean execution — no coaching gaps to flag." Then move on.

5. NEXT STEPS — Single merged list combining actions and follow-ups. Rules:
   - Every item: <strong style="color:#e0e0e0;">Owner</strong> — action — deadline.
   - If no deadline was stated, write "no date — needs one."
   - COMBINE related items into single bullets. Three sends to the same person = one bullet.
   - Tag director-level items with 🔴.
   - Target 4–6 bullets per meeting. If you exceed 7, you are being too granular — consolidate.

6. MARKET INTEL — Competitor mentions, pricing objections, feature requests, vulnerable competitor signals, or industry intelligence. If nothing surfaced, omit this section entirely.
</per_meeting_structure>

<closing_sections>
After all meetings, output exactly two closing sections:

🔴 DIRECTOR PRIORITIES — TODAY
- Maximum 3 items. ONLY things requiring the Sales Director's personal intervention, approval, or decision today. AE-level execution does not belong here.
- Each item: what to do, why it's urgent, which rep/deal. 2-3 sentences max per item.
- If fewer than 3 warrant director action, list fewer. Do NOT pad.

📊 PIPELINE PULSE
- 3-5 sentences. Overall trajectory across all meetings. Deals accelerating or slipping? Patterns across reps — same objection, same competitor, systemic gaps? End with one cross-meeting insight the director might not have connected from individual 1:1s.
</closing_sections>"""


# ── Claude API ───────────────────────────────────────────────────────────────

def summarize_with_claude(meetings_text):
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


# ── Email ────────────────────────────────────────────────────────────────────

def wrap_html(body_content, digest_date):
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background-color:#111111; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#111111;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="680" cellpadding="0" cellspacing="0" style="max-width:680px; width:100%;">

<tr><td style="background:linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius:12px 12px 0 0; padding:32px 36px 24px;">
  <h1 style="margin:0 0 4px; font-size:22px; font-weight:700; color:#e0e0e0; letter-spacing:-0.3px; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">Morning Briefing</h1>
  <p style="margin:0; font-size:14px; color:#888888; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">{digest_date}</p>
</td></tr>

<tr><td style="background-color:#1a1a1a; padding:28px 36px 36px; border-radius:0 0 12px 12px; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  {body_content}
</td></tr>

<tr><td style="padding:20px 36px 8px; text-align:center;">
  <p style="margin:0; font-size:12px; color:#555555; font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
    Generated by Claude Opus &middot; Granola &rarr; Anthropic API &middot; GitHub Actions
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

    print(f"Digest emailed to {EMAIL_TO}")


# ── Digest Storage ───────────────────────────────────────────────────────────

def save_digest(digest_date_str, meeting_titles, digest_html):
    DIGEST_DIR.mkdir(exist_ok=True)

    digest_data = {
        "date": digest_date_str,
        "meeting_count": len(meeting_titles),
        "meetings": meeting_titles,
        "analysis_html": digest_html,
    }

    filepath = DIGEST_DIR / f"{digest_date_str}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(digest_data, f, ensure_ascii=False, indent=2)

    print(f"Digest saved to {filepath}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    check_config()

    print("Fetching yesterday's meeting notes from Granola...")
    notes = fetch_yesterday_notes()

    if not notes:
        print("No meetings found from yesterday. Skipping digest.")
        return

    print(f"Found {len(notes)} meeting(s). Fetching transcripts...")

    meeting_titles = []
    meetings_text_parts = []
    for note in notes:
        note_id = note.get("id", "unknown")
        title = note.get("title", "Untitled Meeting")
        meeting_titles.append(title)
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

    meetings_text = ("\n" + "=" * 60 + "\n\n").join(meetings_text_parts)

    print("Generating briefing with Claude Opus...")
    digest_html = summarize_with_claude(meetings_text)

    if not digest_html:
        print("ERROR: Failed to generate briefing.")
        sys.exit(1)

    now_ct = datetime.now(CT)
    digest_date = now_ct.strftime("%A, %B %-d, %Y")
    subject = f"Morning Briefing — {digest_date}"
    full_html = wrap_html(digest_html, digest_date)

    print("Sending email...")
    send_email(subject, full_html)

    # Save for prep pipeline
    yesterday_ct = now_ct - timedelta(days=1)
    save_digest(yesterday_ct.strftime("%Y-%m-%d"), meeting_titles, digest_html)

    print("Done!")


if __name__ == "__main__":
    main()
