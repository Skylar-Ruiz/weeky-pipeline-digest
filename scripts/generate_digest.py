#!/usr/bin/env python3
"""
Delight's Weekly Digest — automated report generator

Runs every Monday at 5:15am.
Downloads 3 CSVs from Google Drive via rclone, generates the HTML digest via Claude API,
pushes to a review branch on GitHub for Skylar to approve before merging.

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
REPO_PATH     = "/Users/skylar.ruiz/weeky-pipeline-digest"
DRIVE_FOLDER  = "gdrive:Analytics/Weekly AI Briefings /Weekly Digest - Exec Summary"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

EXPECTED_CSVS = [
    "full_funnel_report_daily_export",
    "marketing_okr_progress_daily_export",
    "full_funnel_channels_daily_export",
]

# Q1 FY2027 start date (adjust if needed)
Q1_START = datetime(2026, 2, 2)
# ─────────────────────────────────────────────────────────────────────────────


def download_csvs() -> dict:
    """Download the 3 CSVs from Google Drive using rclone."""
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            ["rclone", "copy", DRIVE_FOLDER, tmp, "-v"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"rclone error: {result.stderr.strip()}")

        csvs = {}
        for path in glob.glob(f"{tmp}/*"):
            base_name = os.path.basename(path).replace(".csv", "").replace(".CSV", "")
            if any(expected in base_name for expected in EXPECTED_CSVS):
                with open(path, encoding="utf-8", errors="replace") as f:
                    csvs[base_name] = f.read()
                print(f"  ✓ Downloaded: {base_name}")

        missing = [e for e in EXPECTED_CSVS if not any(e in k for k in csvs)]
        if missing:
            raise RuntimeError(f"Missing CSVs from Drive: {missing}")

        return csvs


def load_previous_report() -> str:
    """Return the HTML of the most recent digest as a structural template."""
    files = sorted(glob.glob(f"{REPO_PATH}/Weekly_Dashboard_Digest_-_*.html"))
    if not files:
        raise FileNotFoundError("No existing digest files found in repo")
    latest = files[-1]
    print(f"  ✓ Template: {os.path.basename(latest)}")
    with open(latest) as f:
        return f.read()


def generate_report(csvs: dict, template_html: str, week_label: str, week_num: int) -> str:
    """Send CSVs + template to Claude and get back a complete HTML report."""
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

    print("  ✓ Calling Claude API (this may take ~30s)...")
    resp = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
    )
    html = resp.content[0].text.strip()

    # Strip accidental markdown fences if present
    if html.startswith("```"):
        html = html.split("\n", 1)[1]
        html = html.rsplit("```", 1)[0].strip()

    return html


def update_index(filename: str, week_label: str, week_num: int):
    """Prepend the new digest entry to the archive list in index.html."""
    index_path = f"{REPO_PATH}/index.html"
    with open(index_path) as f:
        html = f.read()

    new_entry = f"""  <li class="digest-item">
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
    # Insert as first item in the digest list
    html = html.replace('<ul class="digest-list">\n', f'<ul class="digest-list">\n{new_entry}', 1)
    with open(index_path, "w") as f:
        f.write(html)
    print("  ✓ Updated index.html")


def git_push(filename: str, week_label: str):
    """Create a review branch, commit the new digest, and push to GitHub."""
    branch = f"digest/{week_label.replace(' ', '-').replace(',', '')}"

    def run(cmd, check=True):
        result = subprocess.run(cmd, cwd=REPO_PATH, capture_output=True, text=True)
        if check and result.returncode != 0:
            raise RuntimeError(f"Git error ({' '.join(cmd)}): {result.stderr.strip()}")
        return result

    run(["git", "checkout", "main"])
    run(["git", "pull", "origin", "main"])
    run(["git", "checkout", "-b", branch])
    run(["git", "add", filename, "index.html"])
    run(["git", "commit", "-m", f"Add Weekly Digest for {week_label}"])
    run(["git", "push", "origin", branch])

    print(f"  ✓ Branch pushed: {branch}")
    print(f"  → Review PR: https://github.com/Skylar-Ruiz/weeky-pipeline-digest/compare/{branch}")


def main():
    now = datetime.now()
    week_label = now.strftime("%B %-d, %Y")          # e.g. "March 16, 2026"
    week_num   = ((now - Q1_START).days // 7) + 1    # e.g. 7
    filename   = f"Weekly_Dashboard_Digest_-_{now.strftime('%b_%-d__%Y')}.html"

    print(f"\n📅  Generating digest: {week_label} (Week {week_num} of 13)")

    print("\n📥 Downloading CSVs from Google Drive...")
    csvs = download_csvs()

    print("\n📄 Loading previous report as template...")
    template = load_previous_report()

    print("\n🤖 Generating report with Claude...")
    html = generate_report(csvs, template, week_label, week_num)

    out_path = f"{REPO_PATH}/{filename}"
    with open(out_path, "w") as f:
        f.write(html)
    print(f"  ✓ Saved: {filename}")

    print("\n📋 Updating archive index...")
    update_index(filename, week_label, week_num)

    print("\n🚀 Pushing review branch to GitHub...")
    git_push(filename, week_label)

    print("\n✅ Done! Open the PR link above to review and merge.\n")


if __name__ == "__main__":
    main()
