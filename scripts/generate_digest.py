#!/usr/bin/env python3
"""
Delight's Weekly Digest — automated report generator

Runs every Monday at 5:15am.
Downloads CSVs from Google Drive via rclone, generates the weekly HTML digest
and Events sub-report via Claude API, pushes to a review branch on GitHub.

Setup (one-time):
  1. pip3 install -r scripts/requirements.txt
  2. Install rclone and run: rclone config  (create a remote named "gdrive")
  3. Set ANTHROPIC_API_KEY in your environment (add to ~/.zshrc)
"""

import os
import base64
import glob
import tempfile
import subprocess
import zipfile
from datetime import datetime

import ssl
import time
import certifi
import anthropic
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Fix SSL cert verification on macOS Python 3.14+
_ssl_context = ssl.create_default_context(cafile=certifi.where())

# ── Config ────────────────────────────────────────────────────────────────────
REPO_PATH            = "/Users/skylar.ruiz/weeky-pipeline-digest"
DRIVE_FOLDER         = "gdrive:Analytics/Weekly AI Briefings /Weekly Digest - Exec Summary"
EVENTS_DRIVE_FOLDER  = "gdrive:Analytics/Weekly AI Briefings /Weekly Digest - Events"
EMAIL_DRIVE_FOLDER   = "gdrive:Analytics/Weekly AI Briefings /Weekly Digest - Email"

# Load .env file from project root if it exists (for scheduled/non-interactive runs)
_env_file = os.path.join(REPO_PATH, ".env")
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                _val = _v.strip()
                if _val:
                    os.environ[_k.strip()] = _val

ANTHROPIC_KEY        = os.environ.get("ANTHROPIC_API_KEY", "")
SLACK_BOT_TOKEN      = os.environ.get("SLACK_BOT_TOKEN", "")

# Slack recipients
SLACK_ALERTS_CHANNEL = "C0ALF87LLCF"   # #skylar-claude-alerts
SLACK_CHARLES        = "U090CFEA4BT"   # Charles Studt (CMO) — Weekly Digest
SLACK_LEXI           = "U052Y8M2AJC"   # Lexi von Schottenstein — Events Digest
SLACK_CHERYL         = "U077CLUJYEQ"   # Cheryl Chai — Email Digest
SLACK_CAROLYN        = "U08RULZDYLB"   # Carolyn Hom — Email Digest

EXPECTED_CSVS = [
    "full_funnel_report_daily_export",
    "marketing_okr_progress_daily_export",
    "full_funnel_channels_daily_export",
]

EVENTS_EXPECTED_CSVS = [
    "full funnel campaigns",
    "bigquery_prod full_funnel_combined",
]

# Q1 FY2027 start date (adjust if needed)
Q1_START = datetime(2026, 2, 2)
# ─────────────────────────────────────────────────────────────────────────────


def download_csvs() -> dict:
    """Download CSVs from both Google Drive folders using rclone."""
    with tempfile.TemporaryDirectory() as tmp:
        # Download from Exec Summary folder
        result = subprocess.run(
            ["rclone", "copy", DRIVE_FOLDER, tmp, "-v"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"rclone error (exec summary): {result.stderr.strip()}")

        # Download from Events folder (adds full_funnel_campaigns)
        result = subprocess.run(
            ["rclone", "copy", EVENTS_DRIVE_FOLDER, tmp, "-v"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"rclone error (events): {result.stderr.strip()}")

        # Download from Email folder (adds Weekly_email_report_csv + Weekly_email_report_pdf)
        result = subprocess.run(
            ["rclone", "copy", EMAIL_DRIVE_FOLDER, tmp, "-v"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"rclone error (email): {result.stderr.strip()}")

        all_expected = EXPECTED_CSVS + EVENTS_EXPECTED_CSVS
        csvs = {}
        email_data: dict = {}  # keyed "email_csv" and "email_pdf"

        for path in glob.glob(f"{tmp}/*"):
            base_name = os.path.basename(path)
            base_lower = base_name.lower().replace(".zip", "").replace(".csv", "").replace(".CSV", "")

            # Handle email files separately
            if "weekly_email_report_csv" in base_lower:
                if zipfile.is_zipfile(path):
                    with zipfile.ZipFile(path) as zf:
                        members = [m for m in zf.namelist() if m.lower().endswith(".csv")]
                        parts = []
                        for member in members:
                            with zf.open(member) as f:
                                parts.append(f"=== {os.path.basename(member)} ===\n{f.read().decode('utf-8', errors='replace')}")
                        email_data["email_csv"] = "\n\n".join(parts)
                    print(f"  ✓ Downloaded (email CSV): {base_name} ({len(members)} CSVs)")
                continue

            if "weekly_email_report_pdf" in base_lower:
                with open(path, "rb") as f:
                    email_data["email_pdf"] = base64.standard_b64encode(f.read()).decode("utf-8")
                print(f"  ✓ Downloaded (email PDF): {base_name}")
                continue

            # Newsletter PDFs: last30 (full digest) and last180 (newsletter deep dive)
            if "newsletter_report_last180" in base_lower:
                with open(path, "rb") as f:
                    email_data["newsletter_pdf_180d"] = base64.standard_b64encode(f.read()).decode("utf-8")
                print(f"  ✓ Downloaded (newsletter 180-day PDF): {base_name}")
                continue

            if "newsletter_report_last30" in base_lower:
                with open(path, "rb") as f:
                    email_data["newsletter_pdf_30d"] = base64.standard_b64encode(f.read()).decode("utf-8")
                print(f"  ✓ Downloaded (newsletter 30-day PDF): {base_name}")
                continue

            matched = next((e for e in all_expected if e in base_lower), None)
            if not matched:
                continue

            if zipfile.is_zipfile(path):
                # Read every CSV inside the zip, keyed as "export_name/file.csv"
                with zipfile.ZipFile(path) as zf:
                    members = [m for m in zf.namelist() if m.lower().endswith(".csv")]
                    for member in members:
                        csv_key = f"{base_lower}/{os.path.basename(member)}"
                        with zf.open(member) as f:
                            csvs[csv_key] = f.read().decode("utf-8", errors="replace")
                print(f"  ✓ Downloaded: {base_name} ({len(members)} CSVs)")
            else:
                with open(path, encoding="utf-8", errors="replace") as f:
                    csvs[base_lower] = f.read()
                print(f"  ✓ Downloaded: {base_name}")

        missing = [e for e in EXPECTED_CSVS if not any(e in k for k in csvs)]
        if missing:
            raise RuntimeError(f"Missing CSVs from Drive: {missing}")

        csvs["__email__"] = email_data
        return csvs


def load_previous_report() -> str:
    """Return the HTML of the most recent weekly digest as a structural template."""
    files = sorted(glob.glob(f"{REPO_PATH}/Weekly_Dashboard_Digest_-_*.html"))
    if not files:
        raise FileNotFoundError("No existing digest files found in repo")
    latest = files[-1]
    print(f"  ✓ Weekly template: {os.path.basename(latest)}")
    with open(latest) as f:
        return f.read()


def load_previous_events_report(weekly_template: str) -> str:
    """Return the HTML of the most recent Events report, or weekly digest as fallback."""
    files = sorted(glob.glob(f"{REPO_PATH}/Events_Digest_-_*.html"))
    if files:
        latest = files[-1]
        print(f"  ✓ Events template: {os.path.basename(latest)}")
        with open(latest) as f:
            return f.read()
    print("  ✓ Events template: using weekly digest as structural reference (first run)")
    return weekly_template


def generate_report(csvs: dict, template_html: str, week_label: str, week_num: int, events_filename: str = "") -> str:
    """Send CSVs + template to Claude and get back a complete weekly HTML report."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    # Exclude email data (PDFs/email CSVs) and events-only campaign data — not needed for weekly digest
    weekly_csvs = {k: v for k, v in csvs.items() if k != "__email__" and "campaigns" not in k}

    csv_block = "\n\n".join(
        f"=== {name} ===\n{content}" for name, content in weekly_csvs.items()
    )

    prompt = f"""You are generating Delight's Weekly Digest — a weekly executive pipeline HTML report for delight.ai.

Today: {datetime.now().strftime('%A, %B %-d, %Y')}
Report week: {week_label} · FY2027 Q1 · Week {week_num} of 13
Events Deep Dive filename for this week: {events_filename}

────────────────────────────────────────────────
CSV DATA FROM EXEC DASHBOARDS:
────────────────────────────────────────────────
{csv_block}

────────────────────────────────────────────────
PREVIOUS WEEK'S REPORT (use as exact structural template):
────────────────────────────────────────────────
{template_html}

────────────────────────────────────────────────
INSTRUCTIONS:
────────────────────────────────────────────────
Generate a complete new HTML report for {week_label}. Rules:

1. OUTPUT only the raw HTML document — no markdown fences, no explanation.
2. Keep ALL CSS identical to the template. Do not change any styles.
3. Update <title> and header-date to reflect {week_label} and Week {week_num} of 13.
4. Recalculate all 5 pacing metrics (MQLs, SALs, Opps Created, Discovery, Qualified):
   - IMPORTANT: "Pre-Pipe" and "Pre-Pipeline" have been renamed to "Opps Created" — use "Opps Created" everywhere.
   - Projected % = where we'll finish if current pace holds
   - Actual % = what we've hit so far
   - Status thresholds (apply strictly based on Projected %):
     • On Track  = Projected % ≥ 85%
     • Watch     = Projected % 70–84%
     • Behind    = Projected % < 70%
5. Update the Week-over-Week Trends section by comparing this week's numbers
   to the previous week's numbers (read from the template's pacing grid).
6. Rewrite the Executive Summary, Wins, Concerns, Watch Items, and Action Items
   based entirely on this week's CSV data.
7. Update the navigation bar:
   - "← All Digests" stays as index.html
   - "← Previous" should link to the previous digest filename from the template
   - Add an "Events Deep Dive →" pill button linking to {events_filename}
   - The pill button should use class="nav-events" with this CSS (add to the <style> block):
     .nav-events {{ font-size:12px; font-weight:600; color:#fff; background:#1a1a1a;
       text-decoration:none; padding:6px 14px; border-radius:100px; letter-spacing:0.2px;
       transition:background 0.2s ease; }}
     .nav-events:hover {{ background:#b8654a; }}
8. Update the footer date attribution.
9. Keep the hero gradient, logo, and all structural HTML identical.
10. Do NOT include an Events section or Events Pipeline Spotlight section — omit it entirely.
"""

    print("  ✓ Calling Claude API for Weekly Digest (this may take ~30s)...")
    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
    )
    html = resp.content[0].text.strip()

    if html.startswith("```"):
        html = html.split("\n", 1)[1]
        html = html.rsplit("```", 1)[0].strip()

    return html


def generate_events_report(csvs: dict, template_html: str, week_label: str, week_num: int) -> str:
    """Generate an Events-focused sub-report using Claude."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    # Use only events-relevant CSVs
    events_csvs = {k: v for k, v in csvs.items() if any(
        term in k for term in ["channels", "okr", "campaigns", "bigquery_prod"]
    )}

    csv_block = "\n\n".join(
        f"=== {name} ===\n{content}" for name, content in events_csvs.items()
    )

    prompt = f"""You are generating Delight's Events Pipeline Report — a weekly events-focused sub-report for delight.ai.

Today: {datetime.now().strftime('%A, %B %-d, %Y')}
Report week: {week_label} · FY2027 Q1 · Week {week_num} of 13

────────────────────────────────────────────────
CSV DATA:
────────────────────────────────────────────────
{csv_block}

────────────────────────────────────────────────
STRUCTURAL TEMPLATE (match this HTML/CSS exactly):
────────────────────────────────────────────────
{template_html}

────────────────────────────────────────────────
INSTRUCTIONS:
────────────────────────────────────────────────
Generate a complete Events Pipeline Report for {week_label}. Rules:

1. OUTPUT only the raw HTML document — no markdown fences, no explanation.
2. Keep ALL CSS identical to the template. Do not change any styles.
3. Update <title> to "Events Pipeline Report — {week_label}" and header to read
   "Events Pipeline Report" with the date line "Monday, {week_label} · FY2027 Q1 · Week {week_num} of 13".
4. This report is EVENTS-FOCUSED — only show events pipeline data.
   IMPORTANT: "Pre-Pipe" and "Pre-Pipeline" have been renamed to "Opps Created" — use "Opps Created" everywhere.
5. IMPORTANT: "Pre-Pipe" and "Pre-Pipeline" have been renamed to "Opps Created" — use "Opps Created" everywhere.
6. Sections to include (keep each section concise — this report should be digestible, not exhaustive):
   a. Events Pacing — pacing grid for events-attributed metrics (Opps Created ARR, Discovery ARR, Qualified ARR)
   b. Campaign Breakdown — table of top event campaigns with Opps Created ARR, Discovery ARR, and status
   c. Historical Performance (from the bigquery_prod full_funnel_combined CSV) — a tight analysis covering:
      - Quarter-over-quarter pipeline trend (last 4 quarters + current) with total pipeline and ROI multiplier
      - Top 2-3 insights on conversion rates or cost efficiency worth highlighting
      - Keep this to 3-4 key callouts, not a full data dump
   d. Week-over-Week Trends — compare events metrics to the previous week
   e. Wins, Concerns, Watch Items — specific to events performance only
   f. Recommended Actions — events-focused action items only
7. Do NOT include MQLs, SALs, or non-events pipeline data.
7. Update the navigation bar: "← All Digests" stays as index.html,
   "← Previous Events Report" should link to the previous events report filename from the template
   (look for Events_Digest_-_ in the template nav links).
8. Update the footer date attribution.
9. Keep the logo and all structural HTML identical.
10. The hero gradient MUST use delight brand blue: linear-gradient(135deg, #e8efff 0%, #b8cafc 30%, #8facf9 60%, #7092fb 80%, #5577e8 100%).
    Radial overlays: ::before uses rgba(220,235,255,0.7), ::after uses rgba(180,210,255,0.3).
    Link hover color and action number color: #7092fb.
"""

    print("  ✓ Calling Claude API for Events Report (this may take ~30s)...")
    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
    )
    html = resp.content[0].text.strip()

    if html.startswith("```"):
        html = html.split("\n", 1)[1]
        html = html.rsplit("```", 1)[0].strip()

    return html


def update_index(filename: str, week_label: str, week_num: int):
    """Prepend the new weekly digest entry to the archive list in index.html."""
    index_path = f"{REPO_PATH}/index.html"
    with open(index_path) as f:
        html = f.read()

    if f'href="{filename}"' in html:
        print(f"  ✓ index.html already has weekly entry for {filename}, skipping")
        return

    new_entry = f"""  <li class="digest-item" data-type="weekly">
      <a class="digest-link" href="{filename}">
        <div class="digest-info">
          <div class="digest-title">{week_label}</div>
          <div class="digest-meta">
            <span>Week {week_num} of 13</span>
            <span>·</span>
            <span>FY2027 Q1</span>
          </div>
        </div>
        <div class="digest-right">
          <div class="digest-badges">
            <span class="badge watch">New</span>
          </div>
          <span class="digest-arrow">→</span>
        </div>
      </a>
    </li>
"""
    html = html.replace('<ul class="digest-list">\n', f'<ul class="digest-list">\n{new_entry}', 1)
    with open(index_path, "w") as f:
        f.write(html)
    print("  ✓ Updated index.html with weekly entry")


def update_index_events(filename: str, week_label: str):
    """Prepend the new Events report entry to the archive list in index.html."""
    index_path = f"{REPO_PATH}/index.html"
    with open(index_path) as f:
        html = f.read()

    if f'href="{filename}"' in html:
        print(f"  ✓ index.html already has Events entry for {filename}, skipping")
        return

    new_entry = f"""  <li class="digest-item" data-type="events">
      <a class="digest-link" href="{filename}">
        <div class="digest-info">
          <div class="digest-title">Events — {week_label}</div>
          <div class="digest-meta">
            <span>Events Report</span>
            <span>·</span>
            <span>FY2027 Q1</span>
          </div>
        </div>
        <div class="digest-right">
          <div class="digest-badges">
            <span class="badge watch">New</span>
          </div>
          <span class="digest-arrow">→</span>
        </div>
      </a>
    </li>
"""
    html = html.replace('<ul class="digest-list">\n', f'<ul class="digest-list">\n{new_entry}', 1)
    with open(index_path, "w") as f:
        f.write(html)
    print("  ✓ Updated index.html with Events entry")


def load_previous_email_report(events_template: str) -> str:
    """Return the HTML of the most recent Email report, or events digest as fallback."""
    files = sorted(glob.glob(f"{REPO_PATH}/Email_Digest_-_*.html"))
    if files:
        latest = files[-1]
        print(f"  ✓ Email template: {os.path.basename(latest)}")
        with open(latest) as f:
            return f.read()
    print("  ✓ Email template: using events digest as structural reference (first run)")
    return events_template


def generate_email_report(csvs: dict, template_html: str, week_label: str, week_num: int) -> str:
    """Generate an Email-focused sub-report using Claude."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    email_data = csvs.get("__email__", {})
    email_csv = email_data.get("email_csv", "")
    email_pdf_b64 = email_data.get("email_pdf", "")
    newsletter_pdf_30d_b64 = email_data.get("newsletter_pdf_30d", "")
    newsletter_pdf_180d_b64 = email_data.get("newsletter_pdf_180d", "")

    prompt = f"""You are generating Delight's Email Performance Report — a weekly email marketing sub-report for delight.ai.

Today: {datetime.now().strftime('%A, %B %-d, %Y')}
Report week: {week_label} · FY2027 Q1 · Week {week_num} of 13

────────────────────────────────────────────────
CSV DATA FROM EMAIL MARKETING DASHBOARD:
────────────────────────────────────────────────
{email_csv}

────────────────────────────────────────────────
STRUCTURAL TEMPLATE (match this HTML/CSS exactly — change only content and hero color):
────────────────────────────────────────────────
{template_html}

────────────────────────────────────────────────
INSTRUCTIONS:
────────────────────────────────────────────────
Generate a complete Email Performance Report for {week_label}. Rules:

1. OUTPUT only the raw HTML document — no markdown fences, no explanation.
2. Keep ALL CSS structure identical to the template.
3. Update <title> to "Email Performance Report — {week_label}" and header to read
   "Email Performance Report" with date line "Monday, {week_label} · FY2027 Q1 · Week {week_num} of 13".
4. This report is EMAIL-FOCUSED — only show email marketing performance data.
5. HERO GRADIENT — use this dark teal:
   background: linear-gradient(135deg, #e0ede8 0%, #9ec4b8 30%, #5a9688 60%, #2d7060 80%, #1d5248 100%);
   ::before radial: rgba(200,230,220,0.7)
   ::after radial: rgba(150,210,195,0.3)
   All link hover colors and action number colors: #2d7060
6. Sections to include:
   a. Performance Summary — pacing grid with Open Rate, CTR, Unsubscribe Rate vs benchmarks
      (use the 30-day newsletter PDF for overall email KPIs)
   b. Program Breakdown — table of top email programs (clean program names)
   c. Week-over-Week Trends — open rate and CTR across last 4 weeks
   d. Newsletter Deep Dive — Delight Dispatch section:
      - Show 3-month issue scorecards (month, sent, open rate, CTR, total clicks)
        using the 180-day newsletter PDF which covers the last 3 months of newsletter sends
      - Unified ranked links table with issue badge (Mar/Feb/Jan) and click counts
      - Content theme breakdown tiles (Thought Leadership, Customer Stories, Product Releases, Homepage)
        with click counts and % of total from the current month's newsletter
   e. Wins, Concerns, Watch Items — email-specific observations
   f. Recommended Actions — 3-4 email-specific action items
7. Navigation: "← All Digests" → index.html. Previous email report from template nav if exists.
8. Update footer date.
9. Do NOT include pipeline ARR, MQLs, SALs, or non-email data.
10. The 30-day newsletter PDF covers all email types for the past 30 days — use it for sections a, b, c, e, f.
11. The 180-day newsletter PDF covers only newsletter sends for the past 180 days — use it ONLY for section d (Newsletter Deep Dive).
"""

    content = []
    if email_pdf_b64:
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": email_pdf_b64},
            "title": "Weekly Email Marketing Performance Dashboard (all email types, 30-day)"
        })
    if newsletter_pdf_30d_b64:
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": newsletter_pdf_30d_b64},
            "title": "Newsletter Report — 30 days (use for overall email KPIs and program breakdown)"
        })
    if newsletter_pdf_180d_b64:
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": newsletter_pdf_180d_b64},
            "title": "Newsletter Report — 180 days (use ONLY for Newsletter Deep Dive 3-month comparison)"
        })
    content.append({"type": "text", "text": prompt})

    print("  ✓ Calling Claude API for Email Report (this may take ~30s)...")
    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=16000,
        messages=[{"role": "user", "content": content}],
    )
    html = resp.content[0].text.strip()

    if html.startswith("```"):
        html = html.split("\n", 1)[1]
        html = html.rsplit("```", 1)[0].strip()

    return html


def update_index_email(filename: str, week_label: str):
    """Prepend the new Email report entry to the archive list in index.html."""
    index_path = f"{REPO_PATH}/index.html"
    with open(index_path) as f:
        html = f.read()

    if f'href="{filename}"' in html:
        print(f"  ✓ index.html already has Email entry for {filename}, skipping")
        return

    new_entry = f"""  <li class="digest-item" data-type="email">
      <a class="digest-link" href="{filename}">
        <div class="digest-info">
          <div class="digest-title">Email — {week_label}</div>
          <div class="digest-meta">
            <span>Email Report</span>
            <span>·</span>
            <span>FY2027 Q1</span>
          </div>
        </div>
        <div class="digest-right">
          <div class="digest-badges">
            <span class="badge watch">New</span>
          </div>
          <span class="digest-arrow">→</span>
        </div>
      </a>
    </li>
"""
    html = html.replace('<ul class="digest-list">\n', f'<ul class="digest-list">\n{new_entry}', 1)
    with open(index_path, "w") as f:
        f.write(html)
    print("  ✓ Updated index.html with Email entry")


def git_push(weekly_filename: str, events_filename: str, email_filename: str, week_label: str):
    """Create a review branch, commit all three digests, and push to GitHub."""
    branch = f"digest/{week_label.replace(' ', '-').replace(',', '')}"

    def run(cmd, check=True):
        result = subprocess.run(cmd, cwd=REPO_PATH, capture_output=True, text=True)
        if check and result.returncode != 0:
            raise RuntimeError(f"Git error ({' '.join(cmd)}): {result.stderr.strip()}")
        return result

    # Save generated file contents before touching git (handles re-runs cleanly)
    with open(f"{REPO_PATH}/{weekly_filename}") as f:
        weekly_content = f.read()
    with open(f"{REPO_PATH}/{events_filename}") as f:
        events_content = f.read()
    with open(f"{REPO_PATH}/{email_filename}") as f:
        email_content = f.read()
    with open(f"{REPO_PATH}/index.html") as f:
        index_content = f.read()

    # Surgically restore only the output files (leaves generate_digest.py intact)
    for output_file in [weekly_filename, events_filename, email_filename, "index.html"]:
        run(["git", "checkout", "HEAD", "--", output_file], check=False)
    run(["git", "checkout", "main"])
    run(["git", "pull", "origin", "main"])
    run(["git", "branch", "-D", branch], check=False)
    run(["git", "push", "origin", "--delete", branch], check=False)
    run(["git", "checkout", "-b", branch])

    with open(f"{REPO_PATH}/{weekly_filename}", "w") as f:
        f.write(weekly_content)
    with open(f"{REPO_PATH}/{events_filename}", "w") as f:
        f.write(events_content)
    with open(f"{REPO_PATH}/{email_filename}", "w") as f:
        f.write(email_content)
    with open(f"{REPO_PATH}/index.html", "w") as f:
        f.write(index_content)

    run(["git", "add", weekly_filename, events_filename, email_filename, "index.html"])
    run(["git", "commit", "-m", f"Add Weekly Digest, Events Report, and Email Report for {week_label}"])
    run(["git", "push", "origin", branch])

    print(f"  ✓ Branch pushed: {branch}")
    print(f"  → Review PR: https://github.com/Skylar-Ruiz/weeky-pipeline-digest/compare/{branch}")


def slack_notify(weekly_url: str, events_url: str, email_url: str, pr_url: str, week_label: str):
    """Send the #skylar-claude-alerts review message and schedule stakeholder DMs 30 min later."""
    if not SLACK_BOT_TOKEN:
        print("  ⚠ SLACK_BOT_TOKEN not set — skipping Slack notifications")
        return

    client = WebClient(token=SLACK_BOT_TOKEN, ssl=_ssl_context)

    # Immediate alert to Skylar's review channel
    client.chat_postMessage(
        channel=SLACK_ALERTS_CHANNEL,
        text=(
            f"📊 *Weekly Digest ready for review — {week_label}*\n\n"
            f"*Preview:*\n"
            f"• <{weekly_url}|Weekly Dashboard Digest>\n"
            f"• <{events_url}|Events Digest>\n"
            f"• <{email_url}|Email Digest>\n\n"
            f"*Approve & merge:* {pr_url}"
        )
    )
    print("  ✓ Sent #skylar-claude-alerts review message")

    # Stakeholder DMs scheduled 30 minutes from now
    post_at = int(time.time()) + 1800
    stakeholders = [
        (SLACK_CHARLES, f"Hi Charles! This week's <{weekly_url}|Weekly Dashboard Digest> for {week_label} is ready to view. 📊"),
        (SLACK_LEXI,    f"Hi Lexi! This week's <{events_url}|Events Pipeline Digest> for {week_label} is ready to view. 📊"),
        (SLACK_CHERYL,  f"Hi Cheryl! This week's <{email_url}|Email Performance Digest> for {week_label} is ready to view. 📊"),
        (SLACK_CAROLYN, f"Hi Carolyn! This week's <{email_url}|Email Performance Digest> for {week_label} is ready to view. 📊"),
    ]
    for user_id, text in stakeholders:
        client.chat_scheduleMessage(channel=user_id, text=text, post_at=post_at)
    print("  ✓ Scheduled stakeholder DMs (30 min)")


def slack_notify_failure(error_message: str):
    """Send a failure alert to #skylar-claude-alerts."""
    if not SLACK_BOT_TOKEN:
        return
    client = WebClient(token=SLACK_BOT_TOKEN, ssl=_ssl_context)
    client.chat_postMessage(
        channel=SLACK_ALERTS_CHANNEL,
        text=f"❌ *Weekly Digest generation failed* — {error_message}"
    )


def inject_chat_widget(html: str) -> str:
    """Inject the Ask AI chat widget script tag before </body> if not already present."""
    if "chat-widget.js" in html:
        return html
    return html.replace("</body>", '<script src="/chat-widget.js"></script>\n</body>', 1)


def main():
    now = datetime.now()
    week_label      = now.strftime("%B %-d, %Y")
    week_num        = ((now - Q1_START).days // 7) + 1
    weekly_filename = f"Weekly_Dashboard_Digest_-_{now.strftime('%b_%-d__%Y')}.html"
    events_filename = f"Events_Digest_-_{now.strftime('%b_%-d__%Y')}.html"
    email_filename  = f"Email_Digest_-_{now.strftime('%b_%-d__%Y')}.html"

    print(f"\n📅  Generating digests: {week_label} (Week {week_num} of 13)")

    print("\n📥 Downloading CSVs from Google Drive...")
    csvs = download_csvs()

    print("\n📄 Loading previous reports as templates...")
    weekly_template = load_previous_report()
    events_template = load_previous_events_report(weekly_template)
    email_template  = load_previous_email_report(events_template)

    print("\n🤖 Generating Weekly Digest with Claude...")
    weekly_html = inject_chat_widget(generate_report(csvs, weekly_template, week_label, week_num, events_filename))
    with open(f"{REPO_PATH}/{weekly_filename}", "w") as f:
        f.write(weekly_html)
    print(f"  ✓ Saved: {weekly_filename}")

    print("\n🤖 Generating Events Report with Claude...")
    events_html = inject_chat_widget(generate_events_report(csvs, events_template, week_label, week_num))
    with open(f"{REPO_PATH}/{events_filename}", "w") as f:
        f.write(events_html)
    print(f"  ✓ Saved: {events_filename}")

    print("\n🤖 Generating Email Report with Claude...")
    email_html = inject_chat_widget(generate_email_report(csvs, email_template, week_label, week_num))
    with open(f"{REPO_PATH}/{email_filename}", "w") as f:
        f.write(email_html)
    print(f"  ✓ Saved: {email_filename}")

    print("\n📋 Updating archive index...")
    update_index(weekly_filename, week_label, week_num)
    update_index_events(events_filename, week_label)
    update_index_email(email_filename, week_label)

    print("\n🚀 Pushing review branch to GitHub...")
    branch = f"digest/{week_label.replace(' ', '-').replace(',', '')}"
    git_push(weekly_filename, events_filename, email_filename, week_label)

    base = "https://htmlpreview.github.io/?https://github.com/Skylar-Ruiz/weeky-pipeline-digest/blob"
    weekly_url = f"{base}/{branch}/{weekly_filename}"
    events_url = f"{base}/{branch}/{events_filename}"
    email_url  = f"{base}/{branch}/{email_filename}"
    pr_url     = f"https://github.com/Skylar-Ruiz/weeky-pipeline-digest/compare/{branch}"

    print("\n📣 Sending Slack notifications...")
    slack_notify(weekly_url, events_url, email_url, pr_url, week_label)

    print("\n✅ Done! Open the PR link above to review and merge.\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        error_msg = f"{e}\n{traceback.format_exc()}"
        print(f"\n❌ Error: {error_msg}")
        slack_notify_failure(str(e))
        raise
