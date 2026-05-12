#!/usr/bin/env python3
"""
Backfill Digest Archive (one-time use)
Pulls the last 14 days of meetings from Granola, runs each day through the
same Opus analysis prompt, and saves JSON files to digests/.

Run once to bootstrap the archive, then delete this file.
"""

import os
import re
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from collections import defaultdict

# ── Configuration ────────────────────────────────────────────────────────────
GRANOLA_API_KEY = os.environ.get("GRANOLA_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

GRANOLA_BASE = "https://public-api.granola.ai/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-opus-4-6"
CT = ZoneInfo("America/Chicago")
LOOKBACK_DAYS = 14

DIGEST_DIR = Path(__file__).parent / "digests"

# Import the analysis prompt from the main digest script
# (duplicated here so this script is self-contained)
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


def check_config():
    if not GRANOLA_API_KEY or not ANTHROPIC_API_KEY:
        print("ERROR: GRANOLA_API_KEY and ANTHROPIC_API_KEY must be set.")
        sys.exit(1)


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


def fetch_all_notes(days_back):
    """Fetch all notes from the last N days."""
    start = (datetime.now(CT) - timedelta(days=days_back)).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    start_str = start.strftime("%Y-%m-%dT%H:%M:%SZ")

    notes = []
    cursor = None
    while True:
        path = f"/notes?created_after={start_str}"
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

    print(f"Fetched {len(notes)} total notes from the last {days_back} days.")
    return notes


def fetch_transcript(note_id):
    return granola_request(f"/notes/{note_id}?include=transcript")


def group_notes_by_date(notes):
    """Group notes by their creation date (CT)."""
    grouped = defaultdict(list)
    for note in notes:
        # Try to parse the created_at field
        created = note.get("created_at") or note.get("createdAt") or ""
        if created:
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(CT)
                date_str = dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                date_str = "unknown"
        else:
            date_str = "unknown"
        grouped[date_str].append(note)

    # Remove unknowns
    grouped.pop("unknown", None)
    return dict(sorted(grouped.items()))


def analyze_day(date_str, notes):
    """Run one day's meetings through Opus and return the analysis."""
    meetings_text_parts = []
    meeting_titles = []

    for note in notes:
        note_id = note.get("id", "unknown")
        title = note.get("title", "Untitled Meeting")
        meeting_titles.append(title)
        print(f"    → {title}")

        full_note = fetch_transcript(note_id)
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

    # Call Opus
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 16384,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"Analyze these meeting transcripts from {date_str} and produce a briefing:\n\n{meetings_text}"
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
            analysis = "".join(
                block["text"] for block in data.get("content", [])
                if block.get("type") == "text"
            )
            return meeting_titles, analysis
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"  Anthropic API error {e.code}: {body}")
        return meeting_titles, None


def save_digest(date_str, meeting_titles, analysis_html):
    DIGEST_DIR.mkdir(exist_ok=True)
    filepath = DIGEST_DIR / f"{date_str}.json"

    digest_data = {
        "date": date_str,
        "meeting_count": len(meeting_titles),
        "meetings": meeting_titles,
        "analysis_html": analysis_html,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(digest_data, f, ensure_ascii=False, indent=2)

    print(f"  Saved → {filepath}")


def main():
    check_config()
    DIGEST_DIR.mkdir(exist_ok=True)

    # Check which dates already have digests (skip those)
    existing = {f.stem for f in DIGEST_DIR.glob("*.json")}
    if existing:
        print(f"Found {len(existing)} existing digest(s): {', '.join(sorted(existing))}")

    # Fetch all notes
    print(f"\nFetching all meetings from the last {LOOKBACK_DAYS} days...")
    notes = fetch_all_notes(LOOKBACK_DAYS)

    if not notes:
        print("No meetings found. Nothing to backfill.")
        return

    # Group by date
    grouped = group_notes_by_date(notes)
    print(f"\nMeetings span {len(grouped)} day(s): {', '.join(grouped.keys())}")

    # Process each day
    days_processed = 0
    days_skipped = 0

    for date_str, day_notes in grouped.items():
        if date_str in existing:
            print(f"\n[SKIP] {date_str} — digest already exists")
            days_skipped += 1
            continue

        print(f"\n[PROCESSING] {date_str} — {len(day_notes)} meeting(s)")
        meeting_titles, analysis = analyze_day(date_str, day_notes)

        if analysis:
            save_digest(date_str, meeting_titles, analysis)
            days_processed += 1
        else:
            print(f"  WARNING: Analysis failed for {date_str}")

    print(f"\n{'=' * 40}")
    print(f"Backfill complete.")
    print(f"  Days processed: {days_processed}")
    print(f"  Days skipped (already existed): {days_skipped}")
    print(f"  Total digest files: {len(list(DIGEST_DIR.glob('*.json')))}")
    print(f"\nYou can now delete this script (backfill.py) from your repo.")


if __name__ == "__main__":
    main()
