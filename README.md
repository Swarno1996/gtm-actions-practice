# GTM automation practice: a Python script on a GitHub Actions cron

You are rebuilding the n8n lead demo as plain Python that GitHub runs for you on a schedule. Same pipeline, different runner. By the end you will have created a repo, pushed code, stored a secret, triggered a workflow by hand, watched a cron fire, read the logs, and broken it on purpose to see the alerting work.

## 0. What you are building

| n8n demo node | In this repo |
|---|---|
| Form trigger | rows you add to `data/leads.csv` |
| Normalize URL (with the three field intake guard) | `intake_guard()` and `normalize_url()` in `scripts/enrich_leads.py` |
| Fetch homepage | `fetch_homepage()` |
| Enrich and fit (LLM node) | `rule_score()` always, `llm_score()` only when `ANTHROPIC_API_KEY` exists |
| Switch A / B / C plus fallback | `tier_for()` and the routing in `main()` |
| Google Sheets append (Leads tab) | `data/leads_log.csv`, committed back to the repo by the workflow |
| Nurture queue tab | `data/nurture_queue.csv` |
| Gmail tier A alert | `notify()` opens a GitHub issue (swap for Slack or email later) |
| Error trigger workflow | the script exits with code 1, the run turns red, GitHub emails you |
| Daily digest at 09:00 Berlin, ALERT on zero leads | `scripts/daily_digest.py` on its own cron |
| Idempotency (named in the interview, never built) | `lead_id` is a hash of the email; leads already in the log are skipped |

Repo map:

```
gtm-actions-practice/
  README.md                  this guide
  requirements.txt           Python packages the runner installs
  .env.example               names of the environment variables (no values)
  .gitignore                 keeps .venv and .env out of git
  data/leads.csv             the inbox: edit this to add leads
  data/leads_log.csv         created by the script, this is the pipeline's memory
  data/nurture_queue.csv     created by the script, tier B leads
  reports/                   daily digests land here
  scripts/common.py          CSV helpers, .env loading, GitHub issue notify, job summary
  scripts/enrich_leads.py    the main pipeline
  scripts/daily_digest.py    the health digest
  .github/workflows/enrich.yml   cron every 30 minutes plus a manual button
  .github/workflows/digest.yml   cron once a day
```

The one idea to keep in your head: GitHub stores the code, and GitHub Actions rents you a fresh Linux machine for one minute whenever the cron fires. That machine clones the repo, installs Python, runs your script, and is deleted. Because it is deleted, anything the script must remember (which leads are done) has to be written somewhere that survives. Here that "somewhere" is the repo itself: the workflow commits `data/` back after each run. In production that becomes HubSpot, a database, or a Google Sheet, but the principle is the same.

## Prerequisites (install once)

1. A GitHub account.
2. Git for Windows: https://git-scm.com/download/win. Check with `git --version`.
3. Python 3.12 from python.org, with "Add python.exe to PATH" ticked during install. Check with `python --version`.
4. Optional but handy: GitHub CLI from https://cli.github.com, then `gh auth login` once. It lets you create the repo and read run logs from the terminal.

All commands below are for PowerShell. On Mac or Linux replace `.\.venv\Scripts\Activate.ps1` with `source .venv/bin/activate` and backslashes with forward slashes.

## Step 1: Create the empty repo on GitHub

1. Go to https://github.com, click the plus icon top right, choose **New repository**.
2. Repository name: `gtm-actions-practice`.
3. Visibility: **Public** if you want unlimited free Actions minutes, **Private** if you prefer (private repos get a monthly free allowance, 2,000 minutes on the Free plan, and every run here is billed as one minute; see "Costs and limits" below before you leave the 30 minute schedule on).
4. Do **not** tick "Add a README", "Add .gitignore" or "Choose a license". You are pushing existing files and an initialised repo would conflict.
5. Click **Create repository**. Leave the page open, it shows the commands for the next step.

Terminal alternative: `gh repo create gtm-actions-practice --private --source=. --remote=origin --push` from inside the project folder after Step 2 point 3, and you can skip the rest of Step 2.

## Step 2: Put the files on your laptop and push them

1. Unzip `gtm-actions-practice.zip` somewhere sensible, for example `C:\Users\<you>\Projects\`. Windows shows the `.github` folder normally; on Mac press Cmd+Shift+. to see it.
2. Open PowerShell in that folder (right click the folder, "Open in Terminal").
3. Turn the folder into a git repo and make the first commit:

```powershell
git init
git add .
git commit -m "Initial practice repo"
git branch -M main
```

4. Connect it to GitHub and push (copy the URL from the page you left open):

```powershell
git remote add origin https://github.com/<your-user>/gtm-actions-practice.git
git push -u origin main
```

Git may pop up a browser window to sign you in the first time.

5. Refresh the GitHub page. You should see all the files, and an **Actions** tab. Click it: both workflows are listed on the left ("Enrich leads", "Daily digest") because GitHub found the YAML files in `.github/workflows/`. Nothing has run yet.

What just happened: pushing the workflow files to the default branch is what registers the schedules. GitHub only honours `schedule:` triggers on the default branch, so if you ever move the YAML to a feature branch, the cron stops.

## Step 3: Run the script locally first

Observe before you automate. This is exactly the debugging order you already use: reproduce, look, then change.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts\enrich_leads.py
```

If PowerShell refuses to activate the venv, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then try again.

What you should see: one line per lead with a tier, the URL, the HTTP status, a score and the reason. Then a summary line. Then look at the two files the run created:

```powershell
type data\leads_log.csv
type data\nurture_queue.csv
```

Now run it a second time. It prints `Processed 0 new leads`. That is idempotency: every lead in the log is skipped because its `lead_id` (a hash of the email) is already known. Add a new row to `data/leads.csv` (name, work email, website) and run again: only the new row is processed.

Two things to notice in the output. The dead domain lead ends in tier C with `homepage unreachable (error: ConnectionError)` and the run still succeeds; this is the Lead 11 case from the demo, handled inside the script instead of by an error workflow. And GitHub lands in tier B because its homepage mentions manufacturing customers; keyword rules are crude, and tuning `INDUSTRY_SIGNALS` at the top of the script is your first real exercise.

Dry run, which scores but writes nothing:

```powershell
$env:DRY_RUN = "true"; python scripts\enrich_leads.py; Remove-Item Env:DRY_RUN
```

Or copy `.env.example` to `.env`, set `DRY_RUN=true` in it, and run normally. The scripts load `.env` on your laptop; on the runner there is no `.env` and the values come from the workflow file instead.

## Step 4: Read the workflow file

Open `.github/workflows/enrich.yml`. Every line is commented, but here is the map:

* `on: schedule: cron:` is the clock. `*/30 * * * *` means every 30 minutes, in UTC.
* `on: workflow_dispatch:` adds a "Run workflow" button so you never have to wait for the clock while testing. The `dry_run` input becomes a checkbox.
* `permissions:` is what the built in `GITHUB_TOKEN` may do. `contents: write` lets the job commit the log, `issues: write` lets `notify()` open issues. Without this block the token is read only and both would fail with 403.
* `concurrency:` guarantees two runs never write the log at the same time. If the cron fires while a manual run is still going, the second waits.
* `runs-on: ubuntu-latest` is the rented machine.
* The steps run top to bottom: check out the repo, install Python, install packages, run the script, commit `data/`.
* `env:` on the "Run the script" step is how secrets reach the script. `${{ secrets.ANTHROPIC_API_KEY }}` is empty until you create that secret in Step 8, and the script treats empty as "skip the LLM".
* The commit step uses `if: !cancelled()` so it also runs after the script exits with 1. That is deliberate: good leads processed in the same run as a broken row still get saved.

The digest workflow is the same skeleton with a different script and a daily cron.

## Step 5: Run it by hand in GitHub Actions

1. GitHub, **Actions** tab, click **Enrich leads** in the left list.
2. On the right, click the **Run workflow** dropdown, keep branch `main`, tick **dry_run**, click the green **Run workflow** button.
3. A run appears in the list within a few seconds. Click it, then click the `enrich` job to open the log.

Read the log top to bottom. Each step is collapsible. Open "Run the script": you see the same output as on your laptop, and the commit step was skipped because dry run was on.

4. Run it again without the dry run tick. This time the script writes the log and the commit step pushes. Go back to the **Code** tab: there is a new commit by `github-actions[bot]` with the message `chore: update lead log [skip ci]`, and `data/leads_log.csv` now exists in the repo.

Wait, the log already exists from your local run in Step 3? Yes, if you committed it. Delete `data/leads_log.csv` and `data/nurture_queue.csv` locally, commit, push, and run the workflow again to watch the runner create them from scratch. Either way, the key check is the same: the runner and your laptop share one source of truth, the file in the repo.

## Step 6: Where the results show up

After a real run, three places:

* **The run page** has a "Summary" section above the job list. The script writes a markdown table there through `step_summary()`. This is the cheapest dashboard you will ever build.
* **Issues** tab: any tier A lead opened an issue titled `Tier A lead: <name>`. With the starter leads there is none yet, so add a lead you expect to score A (a German industrial group with a `.de` domain is a good test, try `siemens.de`) and run again.
* **Commits**: the bot's commit is the audit trail. Click it to see exactly which rows were added.

Pull the bot's commit before you edit `data/leads.csv` locally again, otherwise your next push is rejected:

```powershell
git pull
```

Editing `data/leads.csv` directly on GitHub (pencil icon, then "Commit changes") is fine too and is closer to how a rep would add a lead.

## Step 7: Let the cron run

Do nothing for 30 minutes. Then open the Actions tab: a run labelled "Scheduled" appears, triggered by `schedule`, not by you.

What to know about GitHub's cron:

* Times are UTC. Berlin is UTC+2 in summer (CEST) and UTC+1 in winter (CET). "09:00 Berlin every weekday" is `0 7 * * 1-5` in summer and `0 8 * * 1-5` in winter; GitHub does not handle the switch for you.
* The shortest interval is 5 minutes.
* Runs can start several minutes late, sometimes more at the top of the hour when everyone's crons fire. Never build something that depends on a run starting on the exact minute.
* Public repos: if nothing is pushed to the repo for 60 days, GitHub disables scheduled workflows and emails you. A new commit switches them back on.
* Only the default branch is scheduled.

Cron cheat sheet (five fields: minute, hour, day of month, month, day of week):

| Expression | Meaning |
|---|---|
| `*/30 * * * *` | every 30 minutes |
| `0 * * * *` | every hour on the hour |
| `0 */2 * * *` | every two hours |
| `0 7 * * 1-5` | 07:00 UTC Monday to Friday |
| `0 6,12 * * *` | 06:00 and 12:00 UTC daily |
| `0 7 1 * *` | 07:00 UTC on the first of each month |

Use https://crontab.guru to check an expression before you commit it.

## Step 8: Add a secret and switch on LLM scoring

This is how API keys reach a script without ever being in the code.

1. Get an API key from https://console.anthropic.com (the script needs only a few cents of credit).
2. GitHub repo, **Settings** tab, left sidebar **Secrets and variables**, then **Actions**.
3. Click **New repository secret**. Name: `ANTHROPIC_API_KEY` (exactly, it must match the name in `enrich.yml`). Secret: paste the key. Click **Add secret**.
4. Add a new lead to `data/leads.csv` and run the workflow. In the log you now see both scores, and the `llm_score` column in the CSV is filled.

Notice that the log never prints the key. GitHub masks any secret value that appears in output with `***`. Locally, put the key in `.env` (which `.gitignore` keeps out of git) or set `$env:ANTHROPIC_API_KEY` in the terminal.

Change the model with a second secret or a plain variable called `CLAUDE_MODEL` if you want; the script defaults to `claude-haiku-4-5-20251001`, the small fast model, which is the right size for a classification job.

## Step 9: Break it on purpose

The n8n error workflow took you a week to prove. Here it takes one edit.

1. Add a row to `data/leads.csv` with an empty website column, for example `Missing website lead,peter.schmidt@example.org,` (note the trailing comma).
2. Commit and run the workflow.

What happens: the intake guard raises `Intake contract broken: missing company_website`, the script prints it, skips that row, still processes any good rows, writes the log, and exits with code 1. The run turns red. GitHub emails you about the failed run (your profile Settings, Notifications, Actions controls this). The commit step still ran, so nothing good was lost. The broken row is not in the log, so it will be retried on every run until you fix it, which is what you want: a red run every 30 minutes until someone looks.

Fix the row and run again: green.

Compare this with the demo, where the same class of bug (a renamed field) silently produced empty rows. The difference is that the guard here fails loudly at the boundary instead of letting empty values travel downstream.

## Step 10: The daily digest

1. Actions tab, **Daily digest**, Run workflow.
2. Open the run: the Summary shows the digest table, the Issues tab has an issue titled `Daily digest <date>: Pipeline healthy: N leads processed`, and `reports/digest_<date>.md` was committed.
3. To see the ALERT branch, run it when nothing has been processed in the last 24 hours, or temporarily change `timedelta(hours=24)` in `scripts/daily_digest.py` to `timedelta(minutes=1)`.

The cron in `digest.yml` is `0 7 * * *`, which is 09:00 Berlin in summer. Change it to `0 8 * * *` at the end of October.

## When it does not run: checklist

* The workflow does not appear in the Actions tab: the YAML is not under `.github/workflows/`, or it has an indentation error. GitHub shows a parse error at the top of the Actions tab when the file is malformed.
* "Run workflow" button is missing: `workflow_dispatch:` is not in the `on:` block, or the file is not on the default branch.
* The cron never fires: the file is not on `main`; the repo has been inactive for 60 days; you are looking at Berlin time instead of UTC.
* The script step fails with `KeyError` or an empty value: the secret name in `env:` does not match the name you created in Settings, character for character.
* The commit step fails with 403: `permissions: contents: write` is missing, or Settings, Actions, General, "Workflow permissions" is set to read only for the repo.
* The push is rejected: someone (you, on the web) pushed while the run was going. `git pull --rebase origin main` in the commit step handles the common case; rerun the workflow.
* Two runs overlapped and clobbered each other: the `concurrency:` block is missing.
* `notify()` returns 403 or 404: `issues: write` is missing from `permissions:`, or issues are disabled for the repo (Settings, General, Features).

To read logs from the terminal instead of the browser: `gh run list`, then `gh run view <id> --log`.

## Costs and limits

* Public repos: Actions is free, no minute cap.
* Private repos on the Free plan: 2,000 minutes a month, and each job is rounded up to a whole minute. Every 30 minutes is 1,440 runs a month, which eats most of the allowance. After the practice, change the cron to `0 */2 * * *` or hourly, or make the repo public.
* One run here takes around 30 seconds: 10 to 15 seconds for checkout and Python, the rest is your script.
* The runner has outbound internet, so it can call HubSpot, Lusha, Dialfire, Slack or Claude. It cannot receive webhooks; for those you need a small server or a serverless function, which is the next practice project.

## Next practice extensions, in order

1. Tune the keyword lists until the four starter leads land in the tiers you expect, then add ten real DACH companies and compare `rule_score` against `llm_score` in the CSV.
2. Replace `notify()` with a Slack incoming webhook: one `requests.post` to a URL stored as a secret called `SLACK_WEBHOOK_URL`.
3. Replace the CSV inbox with a HubSpot search (the snippet from our chat), and the CSV log with a HubSpot property write. The workflow file does not change at all; only the script does. That is the point of separating the runner from the logic.
4. Add `pytest` with three tests (`normalize_url`, `intake_guard`, `tier_for`) and a `test.yml` workflow that runs on every push. Now every change is checked before the cron picks it up.
5. Move the state out of the repo into a Google Sheet or a small Postgres (Supabase or Neon free tiers), so several scripts can share it.
