# SETUP.md: instructions for Claude (setting up a new TodoAI data repository)

You are Claude Code, running in a session on the user's **new, private** TodoAI data repository (created from
the `TodoAIData-template` repository). Your job is to make it work for this user: interview them, write their
triage configuration, connect the Claude subscription to GitHub Actions, run a live test, and hand them what the
phone app needs. Be friendly and brief; the user may be on a phone.

## Ground rules

- Speak the user's language once you know it. Ask **one topic at a time**, offer examples, accept "skip".
- Personal details (names, relations, employer) go **only** in `rules.yml` and `taxonomy.yml`. Never in commit
  messages, never anywhere public.
- Check the repository is **private** (ask the user, or look at the repo settings with your GitHub tools). If it
  is public, stop and ask them to make it private first: it will hold their tasks.
- Never print secrets (tokens) in chat. Deliver them as a file or let the user handle them.
- Commit to the default branch (`main`). The app and the workflows only use `main`. If your session can only push
  to a feature branch, push it, open a pull request and ask the user to merge it.
- Keep changes small and explain each one in a sentence.

## Step 1: check the repository

The repository should contain `CLAUDE.md`, `BRIEF.md`, `rules.yml`, `taxonomy.yml`, `scripts/`, `tests/` and
`.github/workflows/`. If it is empty (the user created a blank repository instead of using the template),
copy everything except `.git/` from https://github.com/ordiovince/TodoAIData-template (public).

Run the checks once: `pip install -r requirements.txt && python3 -m unittest discover -s tests` (all must pass).

## Step 2: interview

Explain in two sentences what this is for: "Triage reads what you capture and decides whether it is Work or
personal, which categories, how urgent. Telling it who is who makes it much more accurate." Then ask, in this
order, summarising each answer back in one line:

1. **Language and time zone.** Which language for the daily brief? Which city / time zone?
   What time do they wake up on weekdays (for the morning brief), or do they want no brief at all?
2. **Work.** Their role and company or field, in one line (goes into `me:`). Do they also have a side activity
   (freelance, association) and should it count as Work or Perso?
3. **Work people.** Manager, team members, close colleagues, important clients or partners: first names (and
   nicknames or initials they use in messages) with roles.
4. **Projects.** Products, projects, clients or codenames they mention in messages, with the short name they use
   and aliases. Any mention of one means Work.
5. **Personal people.** Partner, family (with relation: mother, brother, children...), close friends.
   Nicknames count ("Mum", "Tommy").
6. **Keywords.** Words that clearly mean Work (tools, jargon: "Jira", "standup", "PR", "invoice") or Perso
   ("doctor", "rent", "school", a sports club...). Offer a starting list from what you learned.
7. **Categories.** Propose a list for each scope (8–15 each) based on everything above, e.g. their work domains
   plus Meeting/Admin/Hiring, and personal domains (Home, Health, Kids, Car...). Let them add, remove and rename.
8. **Priorities.** Anything that is always urgent for them (e.g. "anything from my manager is P1", "school
   paperwork is P1"), anything usually low priority, anything that should never become a task.

If they don't know or want to skip something, leave it empty: triage still works and asks for review when unsure.

## Step 3: write the configuration

- `rules.yml`: `me`, `language`, `timezone` (IANA name, e.g. `Europe/Paris`), `people.work`, `people.perso`
  (name -> short role), `projects` (short name -> description with aliases), `keywords.work`, `keywords.perso`.
  Keep the comments at the top. Valid YAML; quote values that contain `:` or quotes.
- `taxonomy.yml`: the agreed categories, `Work:` and `Perso:` lists (Title Case, no duplicates).
- `CLAUDE.md`: keep the whole file. If they gave priority preferences, add them as a short `## Personal rules`
  section at the end (bullets). Don't change the rest: the scripts and the app depend on the exact format.
- `BRIEF.md`: translate the three section headings (`## Focus`, `## Soon`, `## Watch`) into their language.
  Keep everything else.
- `.github/workflows/brief.yml`: set the cron to about **one hour before** their wake-up time, converted to UTC
  (GitHub runs schedules in UTC; mention that it shifts by an hour with daylight saving). Pick a minute that is
  not 0 (e.g. `13`). If they want no brief, delete the `schedule:` block and keep `workflow_dispatch`.

Then run `python3 -m unittest discover -s tests` and `python3 scripts/todoai.py build --strict`, show the user a
short summary of what you configured, and commit: `Configure triage for <first name>` (no other personal
details in the message). Push to `main`.

## Step 4: connect their Claude subscription (CLAUDE_CODE_OAUTH_TOKEN)

Triage and the brief run `claude -p` inside GitHub Actions, authenticated with a long-lived token from their own
Claude Pro or Max subscription.

- **If they have a computer** with Claude Code: they run `claude setup-token` there and copy the token.
- **Otherwise**, if the `claude` CLI is available in your session: run `claude setup-token` in a pseudo-terminal
  (e.g. `script -q -f -c "claude setup-token" out.log` reading input from a FIFO), give the user the sign-in link
  it prints, wait for them to paste back the code, send it followed by Enter. Extract the token (it starts with
  `sk-ant-oat01-`; the terminal output wraps it across lines) into a file, send that file to the user instead of
  printing it, then delete the file once they confirm.

They add it in GitHub: repository **Settings → Secrets and variables → Actions → New repository secret**,
name `CLAUDE_CODE_OAUTH_TOKEN`. Also: **Settings → Actions → General → Workflow permissions → Read and write**.

## Step 5: live test

1. Push one test capture, `inbox/<YYYYMMDD-HHMMSS>-<4 hex>.md` (UTC time now, random hex), in their language,
   mixing a Work and a Perso action using names from the interview, for example:

   ```
   ---
   captured_at: "<UTC now, YYYY-MM-DDTHH:MM:SSZ>"
   source: "quick-add"
   subject: null
   ---
   <e.g. "Ask Alice for the Q3 numbers before Friday, and book the dentist for Tom">
   ```

2. Wait for the **Triage inbox** workflow (about a minute), pull, and show the tasks it created. Check scope,
   categories and dates with the user, and adjust `rules.yml` / `taxonomy.yml` if something is off.
3. Delete the test tasks: remove the `tasks/<id>-*.md` files, run `python3 scripts/todoai.py build`, and commit
   with `[skip ci]` in the message.
4. Optionally run the **Daily brief** workflow once (workflow_dispatch) so they see a brief in the app, but only
   once real tasks exist.

## Step 6: hand over to the app

Tell them to create a **fine-grained personal access token** (GitHub → Settings → Developer settings →
Fine-grained tokens → Generate new token):

- Repository access: **Only select repositories** → this repository
- Permissions → Repository permissions → **Contents: Read and write** (Metadata: read-only is added automatically)

In the TodoAI app's first screen they enter: **Owner** = their GitHub user name, **Repository** = this
repository's name, **Branch** = `main`, and the token. Then: share any text to "Add to TodoAI" or tap **+**.

Finish with a short recap of what you set up and where to change it later (`rules.yml` for people, the app's
Settings for reminders and calendar).
