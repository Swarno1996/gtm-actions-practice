"""Small helpers shared by enrich_leads.py and daily_digest.py.

Nothing in here knows about leads or scoring. It only knows how to
read and write CSV files, load a local .env, and talk to GitHub.
"""

import csv
import os
from pathlib import Path

import requests

# Every path is relative to the repo root, so the scripts work the same
# on your laptop and on the GitHub Actions runner.
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
REPORTS = ROOT / "reports"

INBOX = DATA / "leads.csv"           # you add rows here (the "form submissions")
LOG = DATA / "leads_log.csv"         # the script appends processed leads here
NURTURE = DATA / "nurture_queue.csv" # tier B leads land here

LOG_FIELDS = [
    "lead_id", "processed_at", "name", "work_email", "company_website",
    "normalized_url", "fetch_status", "rule_score", "llm_score", "tier", "reason",
]
NURTURE_FIELDS = ["lead_id", "processed_at", "name", "work_email", "normalized_url", "reason"]


def load_dotenv(path=ROOT / ".env"):
    """Load KEY=VALUE lines from .env into the environment for local runs.

    On GitHub Actions there is no .env file; secrets arrive as environment
    variables from the workflow file instead. Existing variables win.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def read_csv(path):
    """Return a list of dicts, or an empty list if the file does not exist yet."""
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_rows(path, fields, rows):
    """Append rows to a CSV, writing the header first if the file is new."""
    if not rows:
        return
    is_new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def notify(title, body):
    """Alert channel for this practice repo: a GitHub issue.

    Inside GitHub Actions the runner already has GITHUB_TOKEN and
    GITHUB_REPOSITORY, so no extra secret is needed. Locally we just print.
    Later you can swap this function for a Slack webhook or an email.
    """
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        print(f"\n[notify] {title}\n{body}\n")
        return
    response = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"title": title, "body": body},
        timeout=20,
    )
    response.raise_for_status()
    print(f"[notify] issue created: {response.json()['html_url']}")


def step_summary(markdown):
    """Show markdown on the run page in GitHub Actions (the "Summary" box).

    GITHUB_STEP_SUMMARY only exists on the runner, so locally this is a no-op.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")
