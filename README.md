# hireloop

An MCP server that lets Claude find and apply to jobs on [Wellfound](https://wellfound.com)
and [Indeed](https://in.indeed.com) for you. It returns compact JSON so it stays cheap on tokens, browses at human speed,
checks every answer against your profile before sending it, and emails you a record of
everything it does.

- **Search and screen:** searches roles on either site, reads each job, and skips ones that
  break your rules (too much experience, too little pay, wrong country, external apply).
- **Apply:** reads the application form, drafts answers only from your `profile.md`,
  fact-checks them, and submits. With auto mode on, it sends on its own when every check
  passes and holds anything doubtful for you.
- **Track:** an email per application, a held-draft alert, a Sunday report, and a local
  dashboard, covering both sites.

> **Indeed support is new and not yet checked against the live site.** The Wellfound side
> is tested on real pages; see [Indeed](#indeed) before relying on it.

## Setup

Needs Python 3.12–3.14 and Chromium (installed by Playwright). Tested on CachyOS / Arch.

**With uv (recommended):**
```bash
sudo pacman -S uv
git clone https://github.com/rohxn08/hireloop ~/DEV/hireloop
cd ~/DEV/hireloop
uv sync
uv run playwright install chromium
```

**Or with pip:**
```bash
cd ~/DEV/hireloop
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # also installs the hireloop commands
playwright install chromium
```
With pip, drop the `uv run` prefix from the commands below (e.g. `hireloop-dashboard`).

Don't use `playwright install --with-deps`: it needs `apt`. If Chromium can't find a
library, install it with pacman.

**Then, once:**
```bash
cp profile.example.md profile.md      # fill it in: background, skills, preferences
cp .env.example .env                  # optional: email alerts and auto mode (see below)
uv run hireloop-login                 # sign in to Wellfound in the window it opens; it closes itself
uv run hireloop-login indeed          # same for Indeed (optional)
```

Sign in **in the window the login command opens**, not your usual browser: that window's
profile is where the session is saved (one profile holds both sites). If "Sign in with
Google" is refused, use email and password or the site's emailed code.

## Use it from Claude Code

Register the server from the project folder. This uses Claude Code's default **local** scope:
the tools load only when you start `claude` inside this folder, so other projects don't pay
for their tool definitions.
```bash
cd ~/DEV/hireloop
claude mcp add hireloop -- uv run --directory ~/DEV/hireloop hireloop
claude                                 # start Claude Code here to use it
```
(Add `--scope user` instead if you want the tools in every folder, at roughly 1,200 tokens
per message everywhere.) Then ask things like:

- "Find AI engineer and data analyst jobs in Bangalore and apply to the good fits."
- "Search Indeed for ML engineer jobs in Bengaluru and apply to the easy-apply ones."
- "Show me what you skipped and why."
- "Send the held application to Acme, but shorten the answer."

It runs on your computer, so it works from Claude Code here, not from claude.ai on the web.
Don't run `hireloop-login` while Claude Code has the server running: Chromium can't
open the same profile twice.

## Run a batch automatically at login

`scripts/daily-apply.sh` starts Claude Code with the batch instructions in
`scripts/daily-prompt.md`, at most once a day (it records the date in `data/last-daily-run`;
`--force` runs it again). To start it whenever you log in (example for Hyprland's Lua config):

1. `.vscode/tasks.json` (local, gitignored): a task that runs the script when the folder opens.
   ```json
   {"version": "2.0.0", "tasks": [{"label": "Daily job batch", "type": "shell",
     "command": "${workspaceFolder}/scripts/daily-apply.sh",
     "runOptions": {"runOn": "folderOpen"}, "problemMatcher": []}]}
   ```
2. `.vscode/settings.json`: `"task.allowAutomaticTasks": "on"`.
3. `.claude/settings.local.json` (gitignored), so the batch doesn't stop at a permission
   prompt for each tool: `{"permissions": {"allow": ["mcp__hireloop"]}}`.
4. Open VS Code on a spare workspace at login, e.g. in `~/.config/hypr/config/autostart.lua`
   inside the `hyprland.start` block:
   `hl.exec_cmd("sleep 8; hyprctl dispatch exec '[workspace 9 silent] code ~/DEV/hireloop'")`

The batch runs in VS Code's terminal, so you can watch or step in. Only your first login of
the day starts one.

## Tools

| Tool | What it does |
|---|---|
| `check_login(site="wellfound")` | Checks the saved session is still logged in (`site="all"` checks both) |
| `get_profile` | Reads `profile.md` |
| `save_answer(question, answer)` | Adds an answer you gave to `profile.md`, so it isn't asked again |
| `search_jobs(role, location?, remote?, limit=10, summaries=false, site="wellfound")` | Compact rows; hides skipped jobs, marks ones already drafted, held or applied to. `summaries=true` adds a one-line summary per job. Indeed rows carry `easy_apply` |
| `get_job(job_id, full=false)` | Requirements, stack, pay and `fit` against your rules (the fit check always reads the full description). `full=true` also returns the description. Takes the id from search: `4417481-software-engineer` (Wellfound) or `indeed:0123456789abcdef` |
| `skip_job(job_ids, reason?, confirm=false, undo=false)` | Skips jobs (or un-skips them) |
| `draft_application(job_id)` | Opens the form: its questions, any warnings, `fit`, and the job description for writing answers. Nothing is sent |
| `fact_check(job_id, answers)` | Checks the answers against `profile.md` and the posting |
| `hold_application(job_id, answers, reason)` | Keeps a draft unsent and emails you a "Needs review" alert |
| `submit_application(job_id, answers, confirm=false)` | Preview by default; sends with `confirm=true`. On Indeed, may return `new_questions` from a later page |
| `list_applications(status?, site?)` | Your local log: `submitted`, `held`, `drafted` or `skipped` |

All tools except `search_jobs` and `check_login` work out the site from the job id.

## Token use

Results come back as one compact JSON block with empty fields dropped. A search of 10 jobs
is about 400 tokens, checking a job about 90, and the job description (about 250 more) is
only sent with the application form, for jobs you actually apply to. The workflow rules are
stated once in the server's instructions instead of in every tool description.

To keep sessions cheap, start a fresh `claude` session to apply and use `/clear` between
batches. Nothing is lost: the profile, `.env` and the application log live in files.

## Auto mode and the checks

Set `HIRELOOP_AUTO_APPLY=1` in `.env` to let Claude send without asking you each time.
It may only send when **all** of these hold, and the server refuses otherwise:

1. **The job fits your rules:** at most 3 years of experience asked (`HIRELOOP_MAX_YEARS`),
   pay at least ₹4 LPA when listed in rupees (`HIRELOOP_MIN_SALARY_LPA`; Indeed's monthly
   pay is converted to a year), in India or with visa sponsorship, and applied on the site
   itself (on Indeed: "Easily apply" only). Jobs that don't fit are skipped with
   the reason.
2. **Claude reviewed every sentence** against your profile.
3. **`fact_check` passed on those exact answers.** It verifies that every number,
   capitalised name (employer, project, tool, place), link and email appears in your
   profile or the posting, that there's no placeholder text, required questions are
   answered, and answers aren't too long.

What the fact check can't catch is true facts combined wrongly (a real project credited to
the wrong employer). That's what step 2 is for. If anything fails or Claude isn't confident,
the application is **held**: not sent, and emailed to you with the draft and the reason.

With `HIRELOOP_AUTO_APPLY=0` (the default), every application waits for you to approve
its preview. Indeed has a second switch, `HIRELOOP_INDEED_AUTO_APPLY=1`, off by
default while the Indeed side is new; until you set it, Indeed sends wait for your OK even in
auto mode.

## Pacing and limits

- **Undo window.** After "Send application", Wellfound shows the application as
  **Scheduled** with a 5-minute **Undo** button, then delivers it (status **Pending**).
  Undo any application from [your Applied page](https://wellfound.com/jobs/applications).
  Set `HIRELOOP_WAIT_FOR_SCHEDULED=1` to send one at a time, each after the previous
  one's window closes (off by default).
- **Daily cap, per site.** Wellfound starts at 5 a day, rises by 1 for each day you apply
  without Wellfound blocking you, up to 15, and drops back to 5 if Wellfound reports its
  limit. Indeed has its own cap: 10 rising to 30. Indeed doesn't publish a limit, but it
  watches for bot-like applying and restricts accounts, so the cap stays.
- **Wellfound's own limit.** Wellfound caps how many applications can be active at once
  (the number isn't published); slots free up as old ones expire. If you hit it, the tools
  return `limit_reached: true` with Wellfound's message, and Claude stops.
- **Human speed.** Each page load waits 3–7s at random, then scrolls and moves the mouse.
  Answers are typed with uneven timing, and the mouse moves to buttons before clicking.
- **Retries.** Failed page loads (network errors, 429, 5xx) are retried up to 3 times with
  backoff. If a bot check shows (Cloudflare, or Indeed's "Security Check"), the server waits
  up to 2 minutes for you to clear it in the browser window. The final submit is never
  retried, so nothing is sent twice.

## Email alerts

Every submitted application emails you the role, company, link, pay, location, time and
your answers (`[Wellfound] Applied: …` or `[Indeed] Applied: …`). Held drafts send
`[<site>] Needs review: …`, and the weekly report is `[Hireloop] Weekly report: …`.

1. On the Gmail account that sends the alerts, turn on 2-Step Verification, then create an
   **app password** at https://myaccount.google.com/apppasswords. Use that 16-character
   code, not your Gmail or Wellfound password.
2. In `.env`, fill in `HIRELOOP_EMAIL_TO` and `HIRELOOP_SMTP_PASSWORD`. Leave
   `HIRELOOP_SMTP_USER` blank to send from that address to itself.
3. `uv run hireloop-test-email` sends a sample alert.

A failed email never undoes an application; the tool result just says the email failed.
Filter on `subject:("[Wellfound]" OR "[Indeed]" OR "[Hireloop]")` in Gmail to label everything.

## Weekly report

A Sunday 18:00 email with the week's applications and links, held drafts, unsent drafts,
skip reasons and your current daily cap.

```bash
uv run hireloop-weekly-report --dry-run    # preview this week's report
uv run hireloop-weekly-report --install    # set up the Sunday timer (systemd user)
uv run hireloop-weekly-report --uninstall  # remove it
```

The timer is persistent: if you're logged out on Sunday evening, the report goes out the
next time you log in. Each week is sent at most once. `--install` also removes the old
`wellfound-weekly-report` timer if it's there.

## Dashboard

```bash
uv run hireloop-dashboard
```

This opens http://127.0.0.1:8765 in your browser (stop it with Ctrl+C). It shows totals,
today's usage against each site's cap, applications per day for 30 days, the companies you applied
to most, skip reasons, and a filterable list of every job with links. It reads the local log
directly, so **it doesn't need the MCP registered or running**. It only listens on your own
machine and never changes anything. Use `--no-open` to skip opening the browser, or
`HIRELOOP_DASHBOARD_PORT` for another port. Filter the job list by status or by site.

Recruiter replies only show on each site's Applied page; the dashboard knows what hireloop
logged on this computer.

## Settings

Put these in `.env` (gitignored) or the environment; real environment variables win. Each
also works with the old `WELLFOUND_` prefix, so a `.env` from before the rename still works.

| Variable | Default | Purpose |
|---|---|---|
| `HIRELOOP_AUTO_APPLY` | `0` | `1` lets Claude send when every check passes |
| `HIRELOOP_INDEED_AUTO_APPLY` | `0` | Indeed needs this too before it sends without asking; until then each Indeed send waits for your OK |
| `HIRELOOP_MAX_YEARS` | `3` | Most experience a job may ask for |
| `HIRELOOP_MIN_SALARY_LPA` | `4` | Lowest pay (in lakhs a year) when the job lists pay in rupees |
| `HIRELOOP_DAILY_START` / `HIRELOOP_DAILY_LIMIT` | `5` / `15` | Wellfound daily cap: where it starts and how high it can rise |
| `HIRELOOP_INDEED_DAILY_START` / `HIRELOOP_INDEED_DAILY_LIMIT` | `10` / `30` | Indeed daily cap |
| `HIRELOOP_INDEED_URL` | `https://in.indeed.com` | Which Indeed country site to use |
| `HIRELOOP_WAIT_FOR_SCHEDULED` | `0` | Wellfound: `1` waits for the previous application's undo window before sending the next |
| `HIRELOOP_EMAIL_TO` | | Where alerts and reports go |
| `HIRELOOP_SMTP_USER` | `HIRELOOP_EMAIL_TO` | Account that sends them |
| `HIRELOOP_SMTP_PASSWORD` | | Gmail app password for that account |
| `HIRELOOP_HEADLESS` | `0` | `1` hides the browser (bot checks then can't be cleared) |
| `HIRELOOP_MIN_DELAY` / `HIRELOOP_MAX_DELAY` | `3` / `7` | Random pause between page loads, in seconds |
| `HIRELOOP_DASHBOARD_PORT` | `8765` | Dashboard port |
| `HIRELOOP_DATA` | `./data` | Browser session and application log |
| `HIRELOOP_PROFILE` | `./profile.md` | Your profile |

## Your data

Everything stays in the project folder, and `.gitignore` keeps the private parts out of git:

| Path | What it holds |
|---|---|
| `profile.md` | Your background and standard answers (template: `profile.example.md`) |
| `.env` | Settings, including your Gmail app password (template: `.env.example`) |
| `data/browser-profile/` | Your logged-in Wellfound and Indeed sessions. Treat it like a password |
| `data/applications.db` | The log of submitted, held, drafted and skipped jobs |

## Troubleshooting

| Problem | Fix |
|---|---|
| `check_login` says logged out | Close Claude Code, run `uv run hireloop-login [wellfound\|indeed]`, sign in |
| "browser profile is already open" | Close the login window (or Claude Code) and retry |
| "no display found" | Start Claude Code from your desktop session, or set `DISPLAY` |
| A bot check appears | Clear it in the browser window within 2 minutes |
| No alert emails | Run `uv run hireloop-test-email`; Gmail needs an app password |
| Indeed returns `new_questions` | Normal: a later page asked more. Answer, `fact_check` again, resubmit |
| Applied page shows "Scheduled" | Normal: Wellfound delivers it after the 5-minute undo window |

## How it reads Wellfound

Checked against the live site in Nov 2026:

- **Search** reads the Next.js Apollo cache on `/role/<role>`, `/role/r/<role>` (remote) and
  `/role/l/<role>/<city>` pages.
- **Job pages** are read from the Apollo cache when logged in (visa, relocation, skills,
  equity, Wellfound's eligibility check), or from schema.org JSON-LD when logged out.
  Expired jobs return 410.
- **The apply form** opens at `/jobs/<id>-<slug>?autoOpenApplication=true`. Each question is
  a field named `customQuestionAnswers[<key>][answer]`; a trailing `*` marks it required.
  Wellfound attaches your Wellfound profile itself, so nothing is uploaded per application.
- **After sending**, the form closes and the application appears as Scheduled, then Pending.

Only text and textarea questions have appeared so far; select, radio and checkbox handling
is written but untested.

## Indeed

**Status: written, not yet checked against the live site.** Searching, reading jobs and the
apply flow follow Indeed's known page shapes (Nov 2026), with fallbacks, and are covered by
offline tests on pages shaped like Indeed's. Run it in manual mode
(`HIRELOOP_AUTO_APPLY=0`) for the first few Indeed applications and watch the window.

Before using it:
1. `uv run hireloop-login indeed` and sign in.
2. On Indeed, upload your resume and fill in your profile: Indeed Apply sends those, and
   picks the resume you used last.

How it reads Indeed:
- **Search** is `/jobs?q=<role>&l=<place>&start=N` (10 per page, up to 5 pages). Results come
  from the job cards' embedded JSON (`window.mosaic.providerData`), with the card markup as a
  fallback. `remote=true` adds Indeed's remote filter.
- **Job pages** are `/viewjob?jk=<key>`, read from schema.org JSON-LD and `window._initialData`.
  Indeed rarely states experience in a field, so the fit check reads "3+ years" and ranges
  like "2–4 years" from the description.
- **Applying** works only for "Easily apply" jobs; "Apply on company site" ones are skipped as
  external. The Apply button opens Indeed Apply (smartapply.indeed.com) in a new tab, with
  several pages (contact info, resume, employer questions, experience, review). The server
  reads each page's fields generically (labels, names, options), not by Indeed's CSS classes.
  Fields Indeed already filled (contact info, resume) are kept. Questions without
  "(optional)" are treated as required.
- **Drafting** walks forward until the first page with empty required questions and returns
  those. If a later page asks more, `submit_application` stops there, returns them as
  `new_questions`, and adds them to the draft; nothing is sent until every page is answered.
- **Sending** clicks "Submit your application" on the review page once, and waits for
  Indeed's confirmation. It's never retried.

## Development

```bash
uv sync
uv run pytest        # offline tests: both sites' parsers, fact check, store, alerts, dashboard, report
```
