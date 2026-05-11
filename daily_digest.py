#!/usr/bin/env python3
"""
Granola Daily Meeting Digest
Pulls yesterday's meeting notes from Granola, summarizes them via Claude,
and emails the digest.
"""

import os
import sys
import json
import smtplib
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── Configuration (all from environment variables) ──────────────────────────
GRANOLA_API_KEY = os.environ.get("GRANOLA_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
EMAIL_TO = os.environ.get("EMAIL_TO")  # your email address
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")  # email login
SMTP_PASS = os.environ.get("SMTP_PASS")  # app password (not your regular password)

GRANOLA_BASE = "https://public-api.granola.ai/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-20250514"


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
    """Fetch all meeting notes created since yesterday."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
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


def summarize_with_claude(meetings_text):
    """Send meeting transcripts to Claude for summarization."""
    system_prompt = """You are an executive assistant for a sales director at an HCM/payroll software company. 
Your job is to produce a concise, actionable daily meeting digest.

For each meeting, provide:
1. **Meeting title** and attendees (if identifiable)
2. **Summary** — 3-4 sentences max covering what was discussed
3. **Key decisions** — bullet list, only if decisions were actually made
4. **Action items** — bullet list with owner and deadline where stated
5. **Follow-ups needed** — anything unresolved that needs attention

End with a "Priority Actions" section that pulls the most time-sensitive items across all meetings.

Be direct and concise. Skip filler. If a meeting was trivial or purely informational with no actions, say so in one line."""

    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": f"Here are yesterday's meeting transcripts. Summarize them:\n\n{meetings_text}"}
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


def send_email(subject, body_text):
    """Send the digest via SMTP (Gmail or any provider)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO

    # Plain text version
    msg.attach(MIMEText(body_text, "plain"))

    # Simple HTML version (markdown-ish → basic HTML)
    html_body = body_text.replace("\n", "<br>")
    html_body = f"<div style='font-family: -apple-system, sans-serif; font-size: 14px; line-height: 1.6; max-width: 700px;'>{html_body}</div>"
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())

    print(f"Digest emailed to {EMAIL_TO}")


def main():
    check_config()

    # Step 1: Fetch yesterday's notes
    print("Fetching yesterday's meeting notes from Granola...")
    notes = fetch_yesterday_notes()

    if not notes:
        print("No meetings found from yesterday. Skipping digest.")
        return

    print(f"Found {len(notes)} meeting(s). Fetching transcripts...")

    # Step 2: Fetch transcripts for each note
    meetings_text_parts = []
    for note in notes:
        note_id = note.get("id", "unknown")
        title = note.get("title", "Untitled Meeting")
        print(f"  → {title}")

        full_note = fetch_note_with_transcript(note_id)
        if not full_note:
            meetings_text_parts.append(f"## {title}\n[Transcript unavailable]\n")
            continue

        # Build transcript text
        transcript_lines = []
        for entry in full_note.get("transcript", []):
            speaker = entry.get("speaker", {}).get("name") or entry.get("speaker", {}).get("source", "Unknown")
            text = entry.get("text", "")
            transcript_lines.append(f"{speaker}: {text}")

        transcript_text = "\n".join(transcript_lines) if transcript_lines else "[No transcript content]"

        # Include Granola's own summary if available
        granola_summary = full_note.get("summary", "")
        section = f"## {title}\n"
        if granola_summary:
            section += f"### Granola Summary:\n{granola_summary}\n\n"
        section += f"### Transcript:\n{transcript_text}\n"

        meetings_text_parts.append(section)

    meetings_text = "\n---\n\n".join(meetings_text_parts)

    # Step 3: Summarize with Claude
    print("Generating digest with Claude...")
    digest = summarize_with_claude(meetings_text)

    if not digest:
        print("ERROR: Failed to generate summary.")
        sys.exit(1)

    # Step 4: Email the digest
    today = datetime.now().strftime("%A, %B %-d")
    subject = f"Meeting Digest — {today}"

    print("Sending email...")
    send_email(subject, digest)
    print("Done!")


if __name__ == "__main__":
    main()
