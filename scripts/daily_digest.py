"""Daily pipeline health digest, the Python version of the n8n "Daily digest" workflow.

Reads data/leads_log.csv, counts what was processed in the last 24 hours,
flips to ALERT when the count is zero, writes reports/digest_<date>.md,
prints it, and opens a GitHub issue when running in Actions.

Run locally with:  python scripts/daily_digest.py
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from common import LOG, REPORTS, load_dotenv, read_csv, notify, step_summary

BERLIN = ZoneInfo("Europe/Berlin")


def build_digest(rows, now):
    since = now - timedelta(hours=24)
    recent = [
        r for r in rows
        if datetime.fromisoformat(r["processed_at"]).astimezone(BERLIN) >= since
    ]
    counts = {t: sum(1 for r in recent if r["tier"] == t) for t in "ABC"}

    if recent:
        status = f"Pipeline healthy: {len(recent)} leads processed"
    else:
        status = "ALERT: 0 leads processed in the last 24 hours"

    lines = [
        f"## Daily digest {now:%Y-%m-%d %H:%M} Berlin",
        "",
        f"**{status}**",
        "",
        "| Tier | Count |",
        "|---|---|",
        f"| A | {counts['A']} |",
        f"| B | {counts['B']} |",
        f"| C | {counts['C']} |",
        "",
        f"Total leads ever logged: {len(rows)}",
    ]
    if recent:
        lines += ["", "| Tier | Lead | Website | Reason |", "|---|---|---|---|"]
        lines += [
            f"| {r['tier']} | {r['name']} | {r['normalized_url']} | {r['reason']} |"
            for r in recent
        ]
    return status, "\n".join(lines)


def main():
    load_dotenv()
    now = datetime.now(BERLIN)
    rows = read_csv(LOG)
    status, markdown = build_digest(rows, now)

    REPORTS.mkdir(exist_ok=True)
    report_path = REPORTS / f"digest_{now:%Y-%m-%d}.md"
    report_path.write_text(markdown + "\n", encoding="utf-8")

    print(markdown)
    print(f"\nWritten to {report_path}")
    step_summary(markdown)
    notify(f"Daily digest {now:%Y-%m-%d}: {status}", markdown)


if __name__ == "__main__":
    main()
