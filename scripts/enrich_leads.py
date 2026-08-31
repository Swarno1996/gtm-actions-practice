"""Process new leads from data/leads.csv and append them to data/leads_log.csv.

This is the Python version of the n8n demo workflow:

    Form trigger        ->  rows in data/leads.csv
    Normalize URL       ->  intake_guard() + normalize_url()
    Fetch homepage      ->  fetch_homepage()
    Enrich and fit      ->  rule_score() and, if an API key exists, llm_score()
    Switch A/B/C        ->  tier_for() and the routing in main()
    Google Sheets       ->  data/leads_log.csv (committed back to the repo)
    Gmail alert         ->  notify() opens a GitHub issue for tier A
    Error trigger       ->  the script exits with code 1, GitHub marks the run red

Run it locally with:   python scripts/enrich_leads.py
Dry run (no writes):   DRY_RUN=true python scripts/enrich_leads.py
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

from common import (
    INBOX, LOG, NURTURE, LOG_FIELDS, NURTURE_FIELDS,
    load_dotenv, read_csv, append_rows, notify, step_summary,
)

REQUIRED_FIELDS = ["name", "work_email", "company_website"]

# Keyword lists for the rule based score. Tune these after your first runs.
DACH_SIGNALS = ["impressum", "datenschutz", "agb", "gmbh", "aktiengesellschaft"]
INDUSTRY_SIGNALS = [
    "manufactur", "industrial", "industrie", "fertigung", "maschinenbau",
    "energy", "energie", "utilities", "retail", "einzelhandel", "supermarkt",
    "consumer goods", "fmcg", "automotive",
]
SIZE_SIGNALS = [
    "investor", "annual report", "geschäftsbericht", "konzern",
    "worldwide", "weltweit", "standorte", "locations",
]


# --------------------------------------------------------------------------
# Step 1: intake guard and URL normalization (the "Normalize URL" code node)
# --------------------------------------------------------------------------

def intake_guard(row):
    """Fail loudly when a required field is empty, instead of passing junk downstream."""
    missing = [f for f in REQUIRED_FIELDS if not (row.get(f) or "").strip()]
    if missing:
        raise ValueError(f"Intake contract broken: missing {', '.join(missing)}")


def normalize_url(raw):
    """'siemens.com' -> 'https://siemens.com', strips spaces and trailing slashes."""
    url = raw.strip()
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        url = "https://" + url
    return url.rstrip("/")


# --------------------------------------------------------------------------
# Step 2: fetch the homepage (the "Fetch homepage" HTTP Request node)
# --------------------------------------------------------------------------

def fetch_homepage(url):
    """Return (status, plain_text). Dead domains return an error status and empty text."""
    try:
        response = requests.get(
            url,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; gtm-practice-bot/1.0)"},
        )
        if response.status_code >= 400:          # bot blocks (403) and 404s: no text to score
            return f"http {response.status_code}", ""
        html = response.text
        html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip().lower()
        return f"http {response.status_code}", text[:4000]
    except requests.RequestException as exc:
        return f"error: {type(exc).__name__}", ""


# --------------------------------------------------------------------------
# Step 3: score (the "Enrich and fit" node), deterministic first, LLM optional
# --------------------------------------------------------------------------

def rule_score(url, text):
    """Transparent 0 to 5 score. Same input always gives the same output."""
    score, reasons = 0, []
    domain = url.split("/")[2]
    if domain.endswith((".de", ".at", ".ch")) or any(k in text for k in DACH_SIGNALS):
        score += 2
        reasons.append("DACH signal")
    if any(k in text for k in INDUSTRY_SIGNALS):
        score += 2
        reasons.append("target industry")
    if any(k in text for k in SIZE_SIGNALS):
        score += 1
        reasons.append("size signal")
    return score, ", ".join(reasons) or "no ICP signals found"


def llm_score(company, text):
    """Optional second opinion from Claude. Only runs when ANTHROPIC_API_KEY is set.

    Returns (score, reason) or (None, None) when skipped or when the reply
    could not be parsed. The rule score is always the fallback.
    """
    if not os.environ.get("ANTHROPIC_API_KEY") or not text:
        return None, None
    try:
        import anthropic  # imported here so the script runs without the package locally

        client = anthropic.Anthropic()
        prompt = (
            "You score B2B leads for Sharpist, a Berlin company selling leadership "
            "coaching to large enterprises. Ideal customer: industrial manufacturing, "
            "retail, FMCG or energy; 1000+ employees; headquartered in Germany, Austria "
            "or Switzerland.\n\n"
            f"Lead: {company}\nHomepage text (truncated):\n{text[:2500]}\n\n"
            'Reply with JSON only, no prose: {"score": <integer 0 to 5>, "reason": "<one sentence>"}'
        )
        message = client.messages.create(
            model=os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        return int(data["score"]), str(data["reason"])
    except Exception as exc:  # the LLM step must never break the pipeline
        print(f"  llm_score skipped: {type(exc).__name__}: {exc}")
        return None, None


def tier_for(score):
    if score >= 4:
        return "A"
    if score >= 2:
        return "B"
    return "C"


# --------------------------------------------------------------------------
# Step 4: the run itself
# --------------------------------------------------------------------------

def lead_id_for(email):
    """Stable id from the email, so the same lead is never processed twice."""
    return hashlib.sha1(email.strip().lower().encode("utf-8")).hexdigest()[:10]


def main():
    load_dotenv()
    dry_run = os.environ.get("DRY_RUN", "").strip().lower() == "true"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    already_done = {row["lead_id"] for row in read_csv(LOG)}
    inbox = read_csv(INBOX)
    print(f"Inbox: {len(inbox)} rows, already logged: {len(already_done)}, dry run: {dry_run}\n")

    logged, nurture, alerts, errors = [], [], [], []

    for row in inbox:
        lead_id = lead_id_for(row.get("work_email", ""))
        if lead_id in already_done:
            continue

        try:
            intake_guard(row)
        except ValueError as exc:
            errors.append(f"{row.get('name') or '(no name)'}: {exc}")
            print(f"SKIP  {row.get('name') or '(no name)':<24} {exc}")
            continue

        url = normalize_url(row["company_website"])
        status, text = fetch_homepage(url)

        if text:
            r_score, r_reason = rule_score(url, text)
            l_score, l_reason = llm_score(row["name"], text)
        else:
            r_score, r_reason = 0, f"homepage unreachable ({status})"
            l_score, l_reason = None, None

        final_score = l_score if l_score is not None else r_score
        tier = tier_for(final_score)
        reason = f"llm: {l_reason} | rules: {r_reason}" if l_reason else r_reason

        print(f"{tier}     {row['name']:<24} {url:<36} {status:<22} score={final_score}  {reason}")

        record = {
            "lead_id": lead_id, "processed_at": now, "name": row["name"],
            "work_email": row["work_email"], "company_website": row["company_website"],
            "normalized_url": url, "fetch_status": status,
            "rule_score": r_score, "llm_score": "" if l_score is None else l_score,
            "tier": tier, "reason": reason,
        }
        logged.append(record)
        if tier == "A":
            alerts.append(record)
        elif tier == "B":
            nurture.append({k: record[k] for k in NURTURE_FIELDS})

    print(f"\nProcessed {len(logged)} new leads: "
          f"A={sum(r['tier'] == 'A' for r in logged)} "
          f"B={sum(r['tier'] == 'B' for r in logged)} "
          f"C={sum(r['tier'] == 'C' for r in logged)}, errors={len(errors)}")

    if dry_run:
        print("DRY RUN: nothing written, no alerts sent.")
    else:
        append_rows(LOG, LOG_FIELDS, logged)
        append_rows(NURTURE, NURTURE_FIELDS, nurture)
        for record in alerts:
            notify(
                f"Tier A lead: {record['name']}",
                f"**{record['name']}** ({record['work_email']})\n\n"
                f"Website: {record['normalized_url']}\n\nScore: {record['tier']} "
                f"({final_score_text(record)})\n\nReason: {record['reason']}",
            )

    if logged:
        step_summary(
            f"### Enrich run {now}\n\n| Tier | Lead | Score | Reason |\n|---|---|---|---|\n"
            + "\n".join(
                f"| {r['tier']} | {r['name']} | {r['llm_score'] or r['rule_score']} | {r['reason']} |"
                for r in logged
            )
        )

    if errors:
        print("\nIntake errors (fix data/leads.csv, these rows will be retried next run):")
        for e in errors:
            print(f"  {e}")
        step_summary("**Intake errors:**\n\n" + "\n".join(f"* {e}" for e in errors))
        sys.exit(1)  # red run in GitHub Actions, so you get notified


def final_score_text(record):
    return f"llm {record['llm_score']}" if record["llm_score"] != "" else f"rules {record['rule_score']}"


if __name__ == "__main__":
    main()
