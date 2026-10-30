#!/usr/bin/env python3
"""Deterministic tooling for the TodoAIData repository.

Subcommands:
  snapshot              list pending captures (inbox/*.md) into .triage/snapshot.txt
  validate [FILES...]   strictly validate task files (default: untracked tasks/*.md)
  finalize              after triage: check Claude's output, consume or fail snapshotted captures
  archive-stale         move done tasks older than 7 days (and status: archived) to archive/<yyyy>/
  build                 rebuild index.json, archive/<yyyy>/index.json and archive/years.json

Everything here is a pure function of the files on disk, so it is safe to re-run after a
`git reset --hard origin/main` when a push races with another writer.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "inbox"
FAILED = INBOX / "failed"
TASKS = ROOT / "tasks"
ARCHIVE = ROOT / "archive"
TRIAGE = ROOT / ".triage"
SNAPSHOT = TRIAGE / "snapshot.txt"
SKIPPED = TRIAGE / "skipped"
BRIEFS = ROOT / "briefs"
PEOPLE_DIR = TRIAGE / "people"      # Claude's suggestions, one JSON list per capture (ignored by git)
PEOPLE_FILE = ROOT / "people.json"  # pending suggestions, shown in the app's Review tab
PEOPLE_MAX = 30
BRIEF_KEEP_DAYS = 14

INDEX_VERSION = 1
ARCHIVE_AFTER = dt.timedelta(days=7)

CAPTURE_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
TASK_ID = re.compile(r"^(\d{8}-\d{6}-[0-9a-f]{4})-([1-9]\d*)$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

SCOPES = ("Work", "Perso")
PRIORITIES = ("P0", "P1", "P2", "P3")
EFFORTS = ("5m", "15m", "30m", "1h", "2h", "4h", "1d", "2d+")
RECURRENCES = ("daily", "weekdays", "weekly", "biweekly", "monthly", "yearly")
ACTIVE_STATUSES = ("todo", "done")
ARCHIVED_STATUSES = ("done", "archived")

# Canonical frontmatter field order. The Android app writes the same order.
FIELDS = (
    "id", "source_id", "title", "scope", "categories", "proposed_categories", "priority",
    "estimated_effort", "due_date", "recurrence", "status", "needs_review", "possible_duplicate_of",
    "source_app", "created_at", "updated_at", "completed_at", "archived_at",
)
ARCHIVE_ROW_FIELDS = (
    "id", "title", "scope", "categories", "priority", "status", "completed_at", "archived_at",
)


class TaskError(Exception):
    pass


# ---------------------------------------------------------------- parsing

def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise TaskError("missing frontmatter (file must start with '---')")
    end = text.find("\n---\n", 4)
    if end == -1:
        if text.endswith("\n---"):
            end = len(text) - 4
        else:
            raise TaskError("unterminated frontmatter")
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as e:
        raise TaskError(f"invalid YAML: {e}") from None
    if not isinstance(meta, dict):
        raise TaskError("frontmatter is not a mapping")
    return meta, text[end + 5:]


def norm_datetime(v):
    """YAML may hand back datetime/date objects for unquoted values; normalise to strings."""
    if isinstance(v, dt.datetime):
        if v.tzinfo is not None:
            v = v.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return v.replace(microsecond=0).isoformat() + "Z"
    if isinstance(v, dt.date):
        return v.isoformat()
    return v


def parse_task(path: Path) -> tuple[dict, str]:
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    return {k: norm_datetime(v) for k, v in meta.items()}, body


def check_task(meta: dict, path: Path, *, archived: bool, taxonomy: dict | None) -> list[str]:
    """Return a list of problems. taxonomy=None skips the category whitelist check."""
    errs = []

    def need(cond, msg):
        if not cond:
            errs.append(msg)

    unknown = set(meta) - set(FIELDS)
    need(not unknown, f"unknown fields: {sorted(unknown)}")
    tid = meta.get("id")
    m = TASK_ID.match(tid) if isinstance(tid, str) else None
    need(m, f"id {tid!r} must look like YYYYMMDD-HHMMSS-xxxx-N")
    need(path.name == f"{tid}.md", f"file name must be {tid}.md")
    need(m and meta.get("source_id") == m.group(1), "source_id must equal the id without its -N suffix")
    title = meta.get("title")
    need(isinstance(title, str) and title.strip() and len(title) <= 120, "title must be a non-empty string (max 120 chars)")
    scope = meta.get("scope")
    need(scope in SCOPES, f"scope must be one of {SCOPES}")
    for key in ("categories", "proposed_categories"):
        v = meta.get(key)
        need(isinstance(v, list) and all(isinstance(c, str) and c for c in v), f"{key} must be a list of strings")
    cats = meta.get("categories")
    need(isinstance(cats, list) and len(cats) >= 1, "categories must contain at least one entry")
    if taxonomy is not None and scope in SCOPES and isinstance(cats, list):
        allowed = set(taxonomy.get(scope, []))
        bad = [c for c in cats if c not in allowed]
        need(not bad, f"categories {bad} are not in taxonomy.yml for {scope} (use proposed_categories)")
    need(meta.get("priority") in PRIORITIES, f"priority must be one of {PRIORITIES}")
    need(meta.get("estimated_effort") in EFFORTS, f"estimated_effort must be one of {EFFORTS}")
    due = meta.get("due_date")
    need(due is None or (isinstance(due, str) and DATE.match(due)), "due_date must be null or YYYY-MM-DD")
    rec = meta.get("recurrence")
    need(rec is None or rec in RECURRENCES, f"recurrence must be null or one of {RECURRENCES}")
    status = meta.get("status")
    allowed_status = ARCHIVED_STATUSES if archived else ACTIVE_STATUSES
    need(status in allowed_status, f"status must be one of {allowed_status} here")
    need(isinstance(meta.get("needs_review"), bool), "needs_review must be true or false")
    dup = meta.get("possible_duplicate_of")
    need(dup is None or (isinstance(dup, str) and TASK_ID.match(dup)), "possible_duplicate_of must be null or a task id")
    app = meta.get("source_app")
    need(app is None or (isinstance(app, str) and app), "source_app must be null or a string")
    for key in ("created_at", "updated_at"):
        v = meta.get(key)
        need(isinstance(v, str) and DATETIME.match(v), f"{key} must be YYYY-MM-DDTHH:MM:SSZ")
    for key in ("completed_at", "archived_at"):
        v = meta.get(key)
        need(v is None or (isinstance(v, str) and DATETIME.match(v)), f"{key} must be null or YYYY-MM-DDTHH:MM:SSZ")
    if status == "done":
        need(meta.get("completed_at") is not None, "completed_at is required when status is done")
    if archived:
        need(meta.get("archived_at") is not None, "archived_at is required in archive/")
    else:
        need(meta.get("archived_at") is None, "archived_at must be null outside archive/")
    return errs


# ---------------------------------------------------------------- writing

def yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(yaml_scalar(x) for x in v) + "]"
    # JSON string escapes are valid YAML double-quoted escapes.
    return json.dumps(str(v), ensure_ascii=False)


def dump_task(meta: dict, body: str) -> str:
    lines = ["---"] + [f"{k}: {yaml_scalar(meta.get(k))}" for k in FIELDS] + ["---"]
    return "\n".join(lines) + "\n" + body


def write_if_changed(path: Path, data) -> bool:
    """Write JSON only when its content differs, so formatting-only differences never churn commits."""
    if path.exists():
        try:
            if json.loads(path.read_text(encoding="utf-8")) == data:
                return False
        except json.JSONDecodeError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


# ---------------------------------------------------------------- helpers

def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def parse_ts(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def load_taxonomy() -> dict:
    data = yaml.safe_load((ROOT / "taxonomy.yml").read_text(encoding="utf-8")) or {}
    return {scope: list(data.get(scope) or []) for scope in SCOPES}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def untracked_task_files() -> list[Path]:
    out = git("ls-files", "--others", "--exclude-standard", "--", "tasks/")
    return [ROOT / p for p in out.splitlines() if p.endswith(".md")]


def capture_meta(cid: str) -> dict:
    """Frontmatter of inbox/<cid>.md (empty if missing or malformed)."""
    path = INBOX / f"{cid}.md"
    if not path.exists():
        return {}
    try:
        meta, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    except TaskError:
        return {}
    return meta


ATTACHMENT = re.compile(r"^attachments/(\d{8}-\d{6}-[0-9a-f]{4})/[\w.-]+\.(jpg|jpeg|png|webp)$")


def capture_attachments(cid: str) -> list[str]:
    """Image paths listed by the capture (uploaded by the app under attachments/<cid>/)."""
    items = capture_meta(cid).get("attachments") or []
    return [a for a in items if isinstance(a, str) and (m := ATTACHMENT.match(a)) and m.group(1) == cid and (ROOT / a).exists()]


def enrich_new_task(path: Path, cid: str) -> None:
    """Deterministic additions to a task Claude created: where it was shared from, and its images."""
    meta, body = parse_task(path)
    source = capture_meta(cid).get("source")
    if isinstance(source, str) and source and source not in ("quick-add", "task-edit", "share"):
        meta["source_app"] = source
    images = capture_attachments(cid)
    if images and "## Attachments" not in body:
        body = body.rstrip("\n") + "\n\n## Attachments\n" + "".join(f"![]({a})\n" for a in images)
    path.write_text(dump_task(meta, body), encoding="utf-8")


def capture_target(cid: str) -> str | None:
    """The task an edit request targets (`target:` in the capture's frontmatter), or None for a plain capture."""
    path = INBOX / f"{cid}.md"
    if not path.exists():
        return None
    try:
        meta, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    except TaskError:
        return None
    target = meta.get("target")
    return target if isinstance(target, str) and TASK_ID.match(target) else None


def modified_task_files() -> list[Path]:
    out = git("diff", "--name-only", "HEAD", "--", "tasks/")
    return [ROOT / p for p in out.splitlines() if p.endswith(".md") and (ROOT / p).exists()]


def read_snapshot() -> list[str]:
    if not SNAPSHOT.exists():
        return []
    return [line.strip() for line in SNAPSHOT.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------- commands

def consumed_ids() -> set[str]:
    """Capture ids that already have tasks (live or archived) or already failed."""
    ids = {m.group(1) for p in [*TASKS.glob("*.md"), *ARCHIVE.glob("*/*.md")] if (m := TASK_ID.match(p.stem))}
    return ids | {p.stem for p in FAILED.glob("*.md")}


def cmd_snapshot(_args) -> int:
    done = consumed_ids()
    ids = sorted(p.stem for p in INBOX.glob("*.md") if CAPTURE_ID.match(p.stem) and p.stem not in done)
    TRIAGE.mkdir(exist_ok=True)
    SNAPSHOT.write_text("".join(f"{i}\n" for i in ids))
    for p in INBOX.glob("*.md"):
        if not CAPTURE_ID.match(p.stem):
            print(f"warning: ignoring inbox file with a malformed name: {p.name}", file=sys.stderr)
    print(len(ids))
    return 0


def cmd_validate(args) -> int:
    files = [Path(f).resolve() for f in args.files] or untracked_task_files() + modified_task_files()
    taxonomy = load_taxonomy()
    bad = 0
    for path in files:
        try:
            meta, _ = parse_task(path)
            errs = check_task(meta, path, archived=False, taxonomy=taxonomy)
        except TaskError as e:
            errs = [str(e)]
        for e in errs:
            print(f"{path.relative_to(ROOT)}: {e}")
        bad += bool(errs)
    print(f"{len(files) - bad}/{len(files)} task files valid")
    return 1 if bad else 0


def known_people() -> set[str]:
    """Names already in rules.yml (work, perso, or ignored), lower-cased."""
    rules = yaml.safe_load((ROOT / "rules.yml").read_text(encoding="utf-8")) or {}
    people = rules.get("people") or {}
    names = set()
    for key in ("work", "perso"):
        names.update(str(n) for n in (people.get(key) or {}))
    names.update(str(n) for n in (people.get("ignored") or []))
    return {n.strip().lower() for n in names}


def load_people() -> list[dict]:
    if not PEOPLE_FILE.exists():
        return []
    return list((json.loads(PEOPLE_FILE.read_text(encoding="utf-8")) or {}).get("people") or [])


def clean_suggestion(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    scope = raw.get("scope")
    if not (0 < len(name) <= 60) or "\n" in name or scope not in ("Work", "Perso"):
        return None
    return {
        "name": name,
        "scope": scope,
        "role": str(raw.get("role") or "").strip()[:80],
        "why": str(raw.get("why") or "").strip()[:200],
    }


def merge_people(consumed: set[str]) -> None:
    """Adds Claude's suggestions for consumed captures to people.json; drops names rules.yml now knows."""
    known = known_people()
    people = [p for p in load_people() if str(p.get("name", "")).strip().lower() not in known]
    seen = {p["name"].lower() for p in people}
    for cid in sorted(consumed):
        f = PEOPLE_DIR / f"{cid}.json"
        if not f.exists():
            continue
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            print(f"ignored {f.name}: not JSON")
            continue
        tasks = sorted(p.stem for p in TASKS.glob(f"{cid}-*.md"))
        for item in raw if isinstance(raw, list) else []:
            s = clean_suggestion(item)
            if s is None or s["name"].lower() in known or s["name"].lower() in seen:
                continue
            seen.add(s["name"].lower())
            people.append({**s, "task_id": tasks[0] if tasks else None, "seen_at": norm_datetime(capture_meta(cid).get("captured_at"))})
    people = people[-PEOPLE_MAX:]
    if people or PEOPLE_FILE.exists():
        write_if_changed(PEOPLE_FILE, {"version": 1, "people": people})


def cmd_finalize(_args) -> int:
    """Gate Claude's output, then delete consumed captures and move unconsumed ones to inbox/failed/."""
    snapshot = set(read_snapshot())

    # Edit requests: capture id -> the one existing task Claude may modify for it.
    targets = {cid: t for cid in snapshot if (t := capture_target(cid))}
    editable = {f"tasks/{t}.md" for t in targets.values()}

    # Claude may only add new files under tasks/, modify the targets of edit requests,
    # and leave reasons under .triage/skipped/ (ignored by git).
    changed = [p for p in git("diff", "--name-only", "HEAD").splitlines()
               if not (p.startswith("inbox/") and Path(p).stem in snapshot) and p not in editable]
    stray = [p for p in git("ls-files", "--others", "--exclude-standard").splitlines()
             if not (p.startswith("tasks/") and p.endswith(".md"))]
    if changed or stray:
        print("refusing to publish: triage touched files it does not own:", *changed, *stray, sep="\n  ")
        return 1

    new_tasks = untracked_task_files()
    taxonomy = load_taxonomy()
    consumed, failed = set(), 0
    for path in new_tasks:
        try:
            meta, _ = parse_task(path)
            errs = check_task(meta, path, archived=False, taxonomy=taxonomy)
        except TaskError as e:
            meta, errs = {}, [str(e)]
        if meta.get("source_id") not in snapshot:
            errs.append(f"source_id {meta.get('source_id')!r} is not in this run's snapshot")
        if meta.get("status") != "todo":
            errs.append("new tasks must have status todo")
        for e in errs:
            print(f"{path.relative_to(ROOT)}: {e}")
        if errs:
            failed += 1
        else:
            consumed.add(meta["source_id"])
    # Edited targets: same checks, plus identity fields must not move.
    for path in modified_task_files():
        rel = str(path.relative_to(ROOT))
        try:
            meta, _ = parse_task(path)
            errs = check_task(meta, path, archived=False, taxonomy=taxonomy)
            before, _ = split_frontmatter(git("show", f"HEAD:{rel}"))
            before = {k: norm_datetime(v) for k, v in before.items()}
            for key in ("id", "source_id", "created_at"):
                if meta.get(key) != before.get(key):
                    errs.append(f"{key} must not change in an edit")
        except (TaskError, subprocess.CalledProcessError) as e:
            meta, errs = {}, [str(e)]
        for e in errs:
            print(f"{rel}: {e}")
        if errs:
            failed += 1
        else:
            consumed.update(cid for cid, t in targets.items() if t == meta["id"])
    if failed:
        print(f"{failed} invalid task file(s); nothing published")
        return 1
    for path in new_tasks:
        enrich_new_task(path, TASK_ID.match(path.stem).group(1))

    # A capture already consumed in an earlier run (its tasks exist on main) also counts.
    for path in TASKS.glob("*.md"):
        m = TASK_ID.match(path.stem)
        if m and m.group(1) in snapshot:
            consumed.add(m.group(1))

    # Before the captures are deleted: suggestions record when the name was seen.
    merge_people(consumed)

    # Leftovers whose capture was already handled (e.g. re-pushed by the app): just drop them.
    for src in INBOX.glob("*.md"):
        if src.stem in consumed_ids() and src.stem not in snapshot:
            src.unlink()
            print(f"dropped already-handled {src.stem}")

    for cid in sorted(snapshot):
        src = INBOX / f"{cid}.md"
        if not src.exists():
            continue
        if cid in consumed:
            src.unlink()
            print(f"consumed {cid}")
            continue
        reason_file = SKIPPED / f"{cid}.txt"
        reason = reason_file.read_text(encoding="utf-8").strip() if reason_file.exists() else "no task produced"
        FAILED.mkdir(parents=True, exist_ok=True)
        dest = FAILED / src.name
        dest.write_text(src.read_text(encoding="utf-8").rstrip("\n") + f"\n\n<!-- triage: {reason} -->\n", encoding="utf-8")
        src.unlink()
        print(f"failed {cid}: {reason}")
    return 0


def cmd_archive_stale(args) -> int:
    now = parse_ts(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    stamp = now.replace(microsecond=0, tzinfo=None).isoformat() + "Z"
    moved = 0
    for path in sorted(TASKS.glob("*.md")):
        try:
            meta, body = parse_task(path)
        except TaskError as e:
            print(f"skip {path.name}: {e}", file=sys.stderr)
            continue
        status, done_at = meta.get("status"), meta.get("completed_at")
        stale = (status == "done" and isinstance(done_at, str) and DATETIME.match(done_at)
                 and now - parse_ts(done_at) >= ARCHIVE_AFTER)
        if not (stale or status == "archived"):
            continue
        meta["archived_at"] = meta.get("archived_at") or stamp
        meta["updated_at"] = stamp
        dest = ARCHIVE / meta["archived_at"][:4] / path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(dump_task(meta, body), encoding="utf-8")
        path.unlink()
        moved += 1
        print(f"archived {path.stem} -> {dest.relative_to(ROOT)}")
    print(f"{moved} task(s) archived")
    return 0


def collect(folder: Path, *, archived: bool, problems: list[str]) -> list[dict]:
    rows = []
    for path in sorted(folder.glob("*.md")):
        try:
            meta, _ = parse_task(path)
            errs = check_task(meta, path, archived=archived, taxonomy=None)
        except TaskError as e:
            errs = [str(e)]
        if errs:
            problems.extend(f"{path.relative_to(ROOT)}: {e}" for e in errs)
            continue
        rows.append(meta)
    return rows


def cmd_build(args) -> int:
    problems: list[str] = []
    active = collect(TASKS, archived=False, problems=problems)
    rows = [{k: t.get(k) for k in FIELDS if k != "archived_at"} for t in active]
    rows.sort(key=lambda t: t["id"])
    changed = write_if_changed(ROOT / "index.json", {"version": INDEX_VERSION, "tasks": rows})

    years = {}
    year_dirs = sorted(p for p in ARCHIVE.glob("*") if p.is_dir() and re.fullmatch(r"\d{4}", p.name)) if ARCHIVE.exists() else []
    for ydir in year_dirs:
        tasks = collect(ydir, archived=True, problems=problems)
        for t in tasks:
            if t["archived_at"][:4] != ydir.name:
                problems.append(f"{ydir.name}/{t['id']}.md: archived_at year does not match its folder")
        arows = [{k: t.get(k) for k in ARCHIVE_ROW_FIELDS} for t in tasks]
        arows.sort(key=lambda t: (t["archived_at"], t["id"]), reverse=True)
        changed |= write_if_changed(ydir / "index.json", {"version": INDEX_VERSION, "tasks": arows})
        if arows:
            years[ydir.name] = len(arows)
    year_list = [{"year": y, "count": years[y]} for y in sorted(years, reverse=True)]
    changed |= write_if_changed(ARCHIVE / "years.json", {"version": INDEX_VERSION, "years": year_list})

    for p in problems:
        print(f"problem: {p}", file=sys.stderr)
    print(f"{len(rows)} active, {sum(years.values())} archived, {'changed' if changed else 'unchanged'}")
    return 1 if problems and args.strict else 0


def cmd_brief_check(args) -> int:
    """Gate the brief run: only briefs/<date>.md may be added, small and non-empty. Prunes old briefs."""
    expected = f"briefs/{args.date}.md"
    changed = [p for p in git("diff", "--name-only", "HEAD").splitlines()]
    stray = [p for p in git("ls-files", "--others", "--exclude-standard").splitlines() if p != expected]
    if changed or stray:
        print("refusing to publish: the brief run touched other files:", *changed, *stray, sep="\n  ")
        return 1
    path = ROOT / expected
    text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if not text or len(text) > 4000:
        print(f"{expected}: missing, empty or too long ({len(text)} chars)")
        return 1
    cutoff = dt.date.fromisoformat(args.date) - dt.timedelta(days=BRIEF_KEEP_DAYS)
    for old in BRIEFS.glob("*.md"):
        try:
            if dt.date.fromisoformat(old.stem) < cutoff:
                old.unlink()
                print(f"pruned {old.name}")
        except ValueError:
            continue
    print(f"{expected}: ok ({len(text)} chars)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot").set_defaults(fn=cmd_snapshot)
    v = sub.add_parser("validate")
    v.add_argument("files", nargs="*")
    v.set_defaults(fn=cmd_validate)
    sub.add_parser("finalize").set_defaults(fn=cmd_finalize)
    a = sub.add_parser("archive-stale")
    a.add_argument("--now", help="override current time (YYYY-MM-DDTHH:MM:SSZ), for tests")
    a.set_defaults(fn=cmd_archive_stale)
    bc = sub.add_parser("brief-check")
    bc.add_argument("date", help="YYYY-MM-DD (local date of rules.yml timezone)")
    bc.set_defaults(fn=cmd_brief_check)
    b = sub.add_parser("build")
    b.add_argument("--strict", action="store_true", help="exit 1 if any task file is invalid")
    b.set_defaults(fn=cmd_build)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
