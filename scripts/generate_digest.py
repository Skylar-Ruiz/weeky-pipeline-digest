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
import glob
import tempfile
import subprocess
from datetime import datetime

import anthropic

# ── Config ────────────────────────────────────────────────────────────────────
REPO_PATH            = "/Users/skylar.ruiz/weeky-pipeline-digest"
DRIVE_FOLDER         = "gdrive:Analytics/Weekly AI Briefings /Weekly Digest - Exec Summary"
EVENTS_DRIVE_FOLDER  = "gdrive:Analytics/Weekly AI Briefings /Weekly Digest - Events"
ANTHROPIC_KEY        = os.environ.get("ANTHROPIC_API_KEY", "")

EXPECTED_CSVS = [
    "full_funnel_report_daily_export",
    "marketing_okr_progress_daily_export",
    "full_funnel_channels_daily_export",
]

EVENTS_EXPECTED_CSVS = [
    "full funnel campaigns",
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

        all_expected = EXPECTED_CSVS + EVENTS_EXPECTED_CSVS
        csvs = {}
        for path in glob.glob(f"{tmp}/*"):
            base_name = os.path.basename(path).replace(".csv", "").replace(".CSV", "")
            base_lower = base_name.lower()
            if any(expected in base_lower for expected in all_expected):
                with open(path, encoding="utf-8", errors="replace") as f:
                    csvs[base_lower] = f.read()
                print(f"  ✓ Downloaded: {base_name}")

        missing = [e for e in EXPECTED_CSVS if not any(e in k for k in csvs)]
        if missing:
            raise RuntimeError(f"Missing CSVs from Drive: {missing}")

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


def generate_report(csvs: dict, template_html: str, week_label: str, week_num: int) -> str:
    """Send CSVs + template to Claude and get back a complete weekly HTML report."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    csv_block = "\n\n".join(
        f"=== {name} ===\n{content}" for name, content in csvs.items()
    )

    prompt = f"""You are generating Delight's Weekly Digest — a weekly executive pipeline HTML report for delight.ai.

Today: {datetime.now().strftime('%A, %B %-d, %Y')}
Report week: {week_label} · FY2027 Q1 · Week {week_num} of 13

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
4. Recalculate all 5 pacing metrics (MQLs, SALs, Pre-Pipe, Discovery, Qualified):
   - Projected % = where we'll finish if current pace holds
   - Actual % = what we've hit so far
   - Status = Behind / Watch / On Track
5. Update the Week-over-Week Trends section by comparing this week's numbers
   to the previous week's numbers (read from the template's pacing grid).
6. Rewrite the Executive Summary, Wins, Concerns, Watch Items, and Action Items
   based entirely on this week's CSV data.
7. Update the navigation bar: "← All Digests" stays as index.html,
   "← Previous" should link to the previous digest filename from the template.
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
        term in k for term in ["channels", "okr", "campaigns"]
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
5. Sections to include:
   a. Events Pacing — pacing grid for events-attributed metrics (Pre-Pipeline ARR, Discovery ARR, Qualified ARR)
   b. Campaign Breakdown — table of top event campaigns with Pre-Pipeline ARR, Discovery ARR, and status
   c. Week-over-Week Trends — compare events metrics to the previous week
   d. Wins, Concerns, Watch Items — specific to events performance only
   e. Recommended Actions — events-focused action items only
6. Do NOT include MQLs, SALs, or non-events pipeline data.
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


def git_push(weekly_filename: str, events_filename: str, week_label: str):
    """Create a review branch, commit both digests, and push to GitHub."""
    branch = f"digest/{week_label.replace(' ', '-').replace(',', '')}"

    def run(cmd, check=True):
        result = subprocess.run(cmd, cwd=REPO_PATH, capture_output=True, text=True)
        if check and result.returncode != 0:
            raise RuntimeError(f"Git error ({' '.join(cmd)}): {result.stderr.strip()}")
        return result

    run(["git", "checkout", "main"])
    run(["git", "pull", "origin", "main"])
    run(["git", "checkout", "-b", branch])
    run(["git", "add", weekly_filename, events_filename, "index.html"])
    run(["git", "commit", "-m", f"Add Weekly Digest and Events Report for {week_label}"])
    run(["git", "push", "origin", branch])

    print(f"  ✓ Branch pushed: {branch}")
    print(f"  → Review PR: https://github.com/Skylar-Ruiz/weeky-pipeline-digest/compare/{branch}")


def main():
    now = datetime.now()
    week_label      = now.strftime("%B %-d, %Y")
    week_num        = ((now - Q1_START).days // 7) + 1
    weekly_filename = f"Weekly_Dashboard_Digest_-_{now.strftime('%b_%-d__%Y')}.html"
    events_filename = f"Events_Digest_-_{now.strftime('%b_%-d__%Y')}.html"

    print(f"\n📅  Generating digests: {week_label} (Week {week_num} of 13)")

    print("\n📥 Downloading CSVs from Google Drive...")
    csvs = download_csvs()

    print("\n📄 Loading previous reports as templates...")
    weekly_template = load_previous_report()
    events_template = load_previous_events_report(weekly_template)

    print("\n🤖 Generating Weekly Digest with Claude...")
    weekly_html = generate_report(csvs, weekly_template, week_label, week_num)
    with open(f"{REPO_PATH}/{weekly_filename}", "w") as f:
        f.write(weekly_html)
    print(f"  ✓ Saved: {weekly_filename}")

    print("\n🤖 Generating Events Report with Claude...")
    events_html = generate_events_report(csvs, events_template, week_label, week_num)
    with open(f"{REPO_PATH}/{events_filename}", "w") as f:
        f.write(events_html)
    print(f"  ✓ Saved: {events_filename}")

    print("\n📋 Updating archive index...")
    update_index(weekly_filename, week_label, week_num)
    update_index_events(events_filename, week_label)

    print("\n🚀 Pushing review branch to GitHub...")
    git_push(weekly_filename, events_filename, week_label)

    print("\n✅ Done! Open the PR link above to review and merge.\n")


if __name__ == "__main__":
    main()
