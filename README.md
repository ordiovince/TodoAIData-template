# TodoAIData (template)

The data repository behind the **TodoAI** Android app: your tasks as Markdown files, triaged by Claude in
GitHub Actions on your own Claude subscription. No server, no API bill.

**Use this template to create a *private* repository**, then follow `SETUP.md` with Claude Code: it interviews you
(who you work with, your family and friends, your projects) and configures triage for you.

```
inbox/<captureId>.md          raw captures pushed by the app
inbox/failed/                 captures triage could not turn into a task (reason appended)
tasks/<captureId>-<n>.md      open and recently done tasks: YAML frontmatter + Markdown body
archive/<yyyy>/<id>.md        archived tasks (done for 7+ days, or dismissed)
attachments/<captureId>/      images shared with a capture
briefs/<date>.md              Claude's morning brief
index.json, archive/...json   derived indexes (never edit by hand)
rules.yml                     who is who, projects, keywords, language, timezone
taxonomy.yml                  allowed categories per scope
CLAUDE.md                     triage rules        BRIEF.md   daily brief format
scripts/todoai.py             snapshot / validate / finalize / archive-stale / build / brief-check
```

Workflows: **Triage inbox** (on every capture), **Daily brief** (every morning), **Maintain indexes** (nightly
7-day auto-archive and repairs), **Tests**.

Checks: `pip install -r requirements.txt && python3 -m unittest discover -s tests`.
