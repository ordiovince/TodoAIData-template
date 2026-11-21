# Changes

What each update of the template brings to a data repository made from it. To update one, start a Claude Code session
on it and ask Claude to bring it up to date with this template, keeping your own files (`rules.yml`, `taxonomy.yml`,
`people.json`, `tasks/`, `archive/`, `inbox/`, `briefs/`, `index.json`, `status/`, `requests/` and the schedule in
`.github/workflows/brief.yml`).

## November 2026: a new brief on demand

The app's Brief card has "Write a new brief" (and "Write today's brief" when there is none yet). It writes
`<date> again <time>` to `requests/brief`; the brief workflow then writes today's brief again instead of skipping, and
marks the request answered so later runs skip.

- Changed: `.github/workflows/brief.yml`, `scripts/todoai.py` (`brief-check --answered`), `scripts/publish.sh`, tests.
- Needs TodoAI 1.0.37 or later. Keep your own schedule (cron line) in `brief.yml`.

## November 2026: triage waits for Claude's usage limit

Before, a capture shared after the 5-hour or weekly limit was reached failed triage and stayed on "Triaging" until the
workflow was re-run by hand. Now the run records when the limit resets in `status/triage-paused` and leaves the
capture in `inbox/`; the app shows it as **Paused** with that time and starts triage again then (it writes
`requests/triage`, whose push starts the workflow).

- Changed: `.github/workflows/triage.yml`, `scripts/todoai.py` (`paused`), `scripts/publish.sh`, tests, README.
- Needs TodoAI 1.0.30 or later.
- A capture already stuck from before the update: run **Triage inbox** once by hand (Actions tab, Run workflow).
