# Changes

What each update of the template brings to a data repository made from it. To update one, start a Claude Code session
on it and ask Claude to bring it up to date with this template, keeping your own files (`rules.yml`, `taxonomy.yml`,
`people.json`, `tasks/`, `archive/`, `inbox/`, `briefs/`, `index.json`, `status/`, `requests/` and the schedule in
`.github/workflows/brief.yml`).

## November 2026: triage waits for Claude's usage limit

Before, a capture shared after the 5-hour or weekly limit was reached failed triage and stayed on "Triaging" until the
workflow was re-run by hand. Now the run records when the limit resets in `status/triage-paused` and leaves the
capture in `inbox/`; the app shows it as **Paused** with that time and starts triage again then (it writes
`requests/triage`, whose push starts the workflow).

- Changed: `.github/workflows/triage.yml`, `scripts/todoai.py` (`paused`), `scripts/publish.sh`, tests, README.
- Needs TodoAI 1.0.30 or later.
- A capture already stuck from before the update: run **Triage inbox** once by hand (Actions tab, Run workflow).
