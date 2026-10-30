"""End-to-end tests for scripts/todoai.py, each run in a throwaway git repo."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def task(tid, **over):
    meta = {
        "id": tid, "source_id": tid.rsplit("-", 1)[0], "title": "Do the thing", "scope": "Work",
        "categories": ["Backend"], "proposed_categories": [], "priority": "P2", "estimated_effort": "1h",
        "due_date": None, "recurrence": None, "status": "todo", "needs_review": False, "possible_duplicate_of": None,
        "created_at": "2026-10-20T08:00:00Z", "updated_at": "2026-10-20T08:00:00Z",
        "completed_at": None, "archived_at": None,
    }
    meta.update(over)
    lines = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()] + ["---"]
    return "\n".join(lines) + "\n## Notes\nBody.\n\n## Source\n> quote\n"


class Repo(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        for rel in ("scripts/todoai.py", "rules.yml", ".gitignore"):
            (self.dir / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, self.dir / rel)
        # Fixed categories, so the tests don't depend on the user's own taxonomy.yml.
        (self.dir / "taxonomy.yml").write_text("Work:\n  - Backend\n  - Netcode\nPerso:\n  - Finance\n  - Health\n")
        for d in ("inbox", "tasks", "archive", "briefs"):
            (self.dir / d).mkdir()
            (self.dir / d / ".gitkeep").touch()
        self.git("init", "-q")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "root")
        self.commit_all()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.dir, check=True, capture_output=True, text=True).stdout

    def commit_all(self):
        self.git("add", "-A")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "c")

    def run_cmd(self, *args):
        return subprocess.run([sys.executable, "scripts/todoai.py", *args], cwd=self.dir, capture_output=True, text=True)

    def write(self, rel, text):
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def json(self, rel):
        return json.loads((self.dir / rel).read_text(encoding="utf-8"))


class BuildTests(Repo):
    def test_build_indexes_active_and_archive(self):
        self.write("tasks/20261020-080000-aaaa-1.md", task("20261020-080000-aaaa-1"))
        self.write("archive/2026/20261001-080000-bbbb-1.md", task(
            "20261001-080000-bbbb-1", status="done", completed_at="2026-10-02T08:00:00Z",
            archived_at="2026-10-09T08:00:00Z"))
        r = self.run_cmd("build", "--strict")
        self.assertEqual(r.returncode, 0, r.stderr)
        idx = self.json("index.json")
        self.assertEqual(idx["version"], 1)
        self.assertEqual([t["id"] for t in idx["tasks"]], ["20261020-080000-aaaa-1"])
        self.assertNotIn("archived_at", idx["tasks"][0])
        arch = self.json("archive/2026/index.json")["tasks"]
        self.assertEqual(set(arch[0]), {"id", "title", "scope", "categories", "priority", "status",
                                        "completed_at", "archived_at"})
        self.assertEqual(self.json("archive/years.json")["years"], [{"year": "2026", "count": 1}])

    def test_formatting_only_difference_is_not_rewritten(self):
        self.write("tasks/20261020-080000-aaaa-1.md", task("20261020-080000-aaaa-1"))
        self.run_cmd("build")
        compact = json.dumps(self.json("index.json"), separators=(",", ":"))
        self.write("index.json", compact)
        self.assertIn("unchanged", self.run_cmd("build").stdout)
        self.assertEqual((self.dir / "index.json").read_text(), compact)

    def test_invalid_task_is_skipped_and_strict_fails(self):
        self.write("tasks/20261020-080000-aaaa-1.md", task("20261020-080000-aaaa-1", priority="urgent"))
        self.assertEqual(self.run_cmd("build").returncode, 0)
        self.assertEqual(self.json("index.json")["tasks"], [])
        r = self.run_cmd("build", "--strict")
        self.assertEqual(r.returncode, 1)
        self.assertIn("priority", r.stderr)

    def test_recurrence_is_optional_and_validated(self):
        legacy = task("20261020-080000-aaaa-1").replace('recurrence: null\n', '')
        self.write("tasks/20261020-080000-aaaa-1.md", legacy)
        self.write("tasks/20261020-080000-bbbb-1.md", task("20261020-080000-bbbb-1", recurrence="weekly"))
        self.assertEqual(self.run_cmd("build", "--strict").returncode, 0)
        rows = {t["id"]: t for t in self.json("index.json")["tasks"]}
        self.assertIsNone(rows["20261020-080000-aaaa-1"]["recurrence"])
        self.assertEqual(rows["20261020-080000-bbbb-1"]["recurrence"], "weekly")
        self.write("tasks/20261020-080000-bbbb-1.md", task("20261020-080000-bbbb-1", recurrence="hourly"))
        self.assertIn("recurrence", self.run_cmd("build", "--strict").stderr)

    def test_unquoted_yaml_dates_are_normalised(self):
        text = task("20261020-080000-aaaa-1", due_date="2026-11-01").replace(
            '"2026-10-20T08:00:00Z"', "2026-10-20T08:00:00Z").replace('"2026-11-01"', "2026-11-01")
        self.write("tasks/20261020-080000-aaaa-1.md", text)
        self.assertEqual(self.run_cmd("build", "--strict").returncode, 0)
        t = self.json("index.json")["tasks"][0]
        self.assertEqual((t["created_at"], t["due_date"]), ("2026-10-20T08:00:00Z", "2026-11-01"))


class ArchiveTests(Repo):
    def test_archives_done_after_7_days_and_dismissed(self):
        self.write("tasks/20261001-080000-aaaa-1.md", task(
            "20261001-080000-aaaa-1", status="done", completed_at="2026-10-19T00:00:00Z"))
        self.write("tasks/20261001-080000-bbbb-1.md", task(
            "20261001-080000-bbbb-1", status="done", completed_at="2026-10-22T00:00:00Z"))
        self.write("tasks/20261001-080000-cccc-1.md", task("20261001-080000-cccc-1", status="archived"))
        r = self.run_cmd("archive-stale", "--now", "2026-10-27T00:00:00Z")
        self.assertIn("2 task(s) archived", r.stdout)
        self.assertTrue((self.dir / "tasks/20261001-080000-bbbb-1.md").exists())
        moved = (self.dir / "archive/2026/20261001-080000-aaaa-1.md").read_text()
        self.assertIn('archived_at: "2026-10-27T00:00:00Z"', moved)
        self.assertIn("## Source\n> quote", moved)
        self.assertEqual(self.run_cmd("build", "--strict").returncode, 0)
        self.assertEqual(self.json("archive/years.json")["years"], [{"year": "2026", "count": 2}])


class BriefTests(Repo):
    def test_brief_check_accepts_only_todays_brief_and_prunes(self):
        self.write("briefs/2026-10-01.md", "old")
        self.write("briefs/2026-10-20.md", "recent")
        self.commit_all()
        self.write("briefs/2026-10-28.md", "## Focus\n- **Call the bank**: due today.\n")
        r = self.run_cmd("brief-check", "2026-10-28")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse((self.dir / "briefs/2026-10-01.md").exists())
        self.assertTrue((self.dir / "briefs/2026-10-20.md").exists())

    def test_brief_check_refuses_other_changes(self):
        self.write("tasks/20261020-080000-aaaa-1.md", task("20261020-080000-aaaa-1"))
        self.commit_all()
        self.write("briefs/2026-10-28.md", "ok")
        self.write("tasks/20261020-080000-aaaa-1.md", task("20261020-080000-aaaa-1", priority="P0"))
        self.assertEqual(self.run_cmd("brief-check", "2026-10-28").returncode, 1)

    def test_brief_check_refuses_an_empty_brief(self):
        self.write("briefs/2026-10-28.md", "  \n")
        self.assertEqual(self.run_cmd("brief-check", "2026-10-28").returncode, 1)


class FinalizeTests(Repo):
    def capture(self, cid):
        self.write(f"inbox/{cid}.md", '---\ncaptured_at: "2026-10-27T09:00:00Z"\nsource: "quick-add"\nsubject: null\n---\nCall the bank\n')

    def test_consumes_and_fails_captures(self):
        self.capture("20261027-090000-aaaa")
        self.capture("20261027-090000-bbbb")
        self.capture("20261027-090000-cccc")
        self.commit_all()
        self.assertEqual(self.run_cmd("snapshot").stdout.strip(), "3")
        self.capture("20261027-091500-dddd")  # arrives mid-run (seen after a retry reset): left alone
        self.commit_all()
        self.write("tasks/20261027-090000-aaaa-1.md", task("20261027-090000-aaaa-1"))
        self.write("tasks/20261027-090000-aaaa-2.md", task("20261027-090000-aaaa-2", scope="Perso", categories=["Finance"]))
        self.write(".triage/skipped/20261027-090000-bbbb.txt", "just a joke\n")
        r = self.run_cmd("finalize")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse((self.dir / "inbox/20261027-090000-aaaa.md").exists())
        self.assertIn("just a joke", (self.dir / "inbox/failed/20261027-090000-bbbb.md").read_text())
        self.assertIn("no task produced", (self.dir / "inbox/failed/20261027-090000-cccc.md").read_text())
        self.assertTrue((self.dir / "inbox/20261027-091500-dddd.md").exists())

    def test_people_suggestions_are_merged_once_and_known_names_dropped(self):
        self.write("rules.yml", 'people:\n  work:\n    Alice: "team"\n  perso: {}\n  ignored: ["Bob"]\n')
        self.capture("20261027-090000-aaaa")
        self.capture("20261027-090000-bbbb")
        self.write("people.json", json.dumps({"version": 1, "people": [
            {"name": "Alice", "scope": "Work", "role": "x", "why": "y", "task_id": None, "seen_at": None},  # in rules.yml now
            {"name": "Jo", "scope": "Perso", "role": "neighbour", "why": "w", "task_id": None, "seen_at": None},
        ]}))
        self.commit_all()
        self.run_cmd("snapshot")
        self.write("tasks/20261027-090000-aaaa-1.md", task("20261027-090000-aaaa-1"))
        self.write(".triage/people/20261027-090000-aaaa.json", json.dumps([
            {"name": "Sam", "scope": "Work", "role": "QA tester", "why": "Reports the crash"},
            {"name": "jo", "scope": "Perso", "role": "", "why": ""},       # already suggested
            {"name": "bob", "scope": "Work", "role": "", "why": ""},       # ignored in rules.yml
            {"name": "Acme", "scope": "Client", "role": "", "why": ""},     # invalid scope
        ]))
        self.write(".triage/people/20261027-090000-bbbb.json", json.dumps([{"name": "Ghost", "scope": "Work"}]))  # not consumed
        r = self.run_cmd("finalize")
        self.assertEqual(r.returncode, 0, r.stdout)
        people = self.json("people.json")["people"]
        self.assertEqual(["Jo", "Sam"], [p["name"] for p in people])
        self.assertEqual({"name": "Sam", "scope": "Work", "role": "QA tester", "why": "Reports the crash",
                          "task_id": "20261027-090000-aaaa-1", "seen_at": "2026-10-27T09:00:00Z"}, people[1])

    def test_new_tasks_get_source_app_and_attachments(self):
        self.write("inbox/20261027-090000-aaaa.md", '---\ncaptured_at: "2026-10-27T09:00:00Z"\nsource: "com.Slack"\nsubject: null\n'
                   'attachments: ["attachments/20261027-090000-aaaa/1.jpg", "attachments/20261027-090000-ffff/1.jpg"]\n---\nsee screenshot\n')
        (self.dir / "attachments/20261027-090000-aaaa").mkdir(parents=True)
        (self.dir / "attachments/20261027-090000-aaaa/1.jpg").write_bytes(b"\xff\xd8jpeg")
        self.commit_all()
        self.run_cmd("snapshot")
        self.write("tasks/20261027-090000-aaaa-1.md", task("20261027-090000-aaaa-1"))
        r = self.run_cmd("finalize")
        self.assertEqual(r.returncode, 0, r.stdout)
        text = (self.dir / "tasks/20261027-090000-aaaa-1.md").read_text()
        self.assertIn('source_app: "com.Slack"', text)
        self.assertIn("## Attachments\n![](attachments/20261027-090000-aaaa/1.jpg)\n", text)
        self.assertNotIn("ffff", text)  # another capture's image is never linked
        self.assertEqual(self.run_cmd("build", "--strict").returncode, 0)

    def test_already_handled_captures_are_not_retriaged(self):
        self.capture("20261027-090000-aaaa")
        self.capture("20261027-090000-bbbb")
        self.write("tasks/20261027-090000-aaaa-1.md", task("20261027-090000-aaaa-1"))
        self.write("inbox/failed/20261027-090000-bbbb.md", "x")
        self.capture("20261027-090000-cccc")
        self.commit_all()
        self.assertEqual(self.run_cmd("snapshot").stdout.strip(), "1")
        self.write("tasks/20261027-090000-cccc-1.md", task("20261027-090000-cccc-1"))
        r = self.run_cmd("finalize")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(sorted(p.name for p in (self.dir / "inbox").glob("*.md")), [])

    def edit_request(self, cid, target):
        self.write(f"inbox/{cid}.md", f'---\ncaptured_at: "2026-10-27T10:00:00Z"\nsource: "task-edit"\nsubject: null\ntarget: "{target}"\n---\nMove it to Friday\n')

    def test_edit_request_may_change_only_its_target(self):
        self.write("tasks/20261020-080000-eeee-1.md", task("20261020-080000-eeee-1"))
        self.write("tasks/20261020-080000-ffff-1.md", task("20261020-080000-ffff-1"))
        self.edit_request("20261027-100000-aaaa", "20261020-080000-eeee-1")
        self.commit_all()
        self.run_cmd("snapshot")
        self.write("tasks/20261020-080000-eeee-1.md", task("20261020-080000-eeee-1", due_date="2026-10-30",
                                                           updated_at="2026-10-27T10:00:00Z"))
        self.assertEqual(self.run_cmd("validate").returncode, 0)
        r = self.run_cmd("finalize")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse((self.dir / "inbox/20261027-100000-aaaa.md").exists())
        self.assertFalse((self.dir / "inbox/failed/20261027-100000-aaaa.md").exists())

    def test_edit_request_cannot_touch_other_tasks_or_identity(self):
        self.write("tasks/20261020-080000-eeee-1.md", task("20261020-080000-eeee-1"))
        self.write("tasks/20261020-080000-ffff-1.md", task("20261020-080000-ffff-1"))
        self.edit_request("20261027-100000-aaaa", "20261020-080000-eeee-1")
        self.commit_all()
        self.run_cmd("snapshot")
        self.write("tasks/20261020-080000-ffff-1.md", task("20261020-080000-ffff-1", priority="P0"))
        r = self.run_cmd("finalize")
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not own", r.stdout)
        self.git("checkout", "--", "tasks/20261020-080000-ffff-1.md")
        self.write("tasks/20261020-080000-eeee-1.md", task("20261020-080000-eeee-1", created_at="2026-10-27T10:00:00Z"))
        r = self.run_cmd("finalize")
        self.assertEqual(r.returncode, 1)
        self.assertIn("created_at must not change", r.stdout)

    def test_edit_request_on_missing_target_fails_with_reason(self):
        self.edit_request("20261027-100000-aaaa", "20261020-080000-eeee-1")
        self.commit_all()
        self.run_cmd("snapshot")
        self.write(".triage/skipped/20261027-100000-aaaa.txt", "task is archived\n")
        self.assertEqual(self.run_cmd("finalize").returncode, 0)
        self.assertIn("task is archived", (self.dir / "inbox/failed/20261027-100000-aaaa.md").read_text())

    def test_refuses_edits_to_existing_files(self):
        self.capture("20261027-090000-aaaa")
        self.write("tasks/20261020-080000-eeee-1.md", task("20261020-080000-eeee-1"))
        self.commit_all()
        self.run_cmd("snapshot")
        self.write("tasks/20261020-080000-eeee-1.md", task("20261020-080000-eeee-1", status="done",
                                                           completed_at="2026-10-27T00:00:00Z"))
        r = self.run_cmd("finalize")
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not own", r.stdout)
        self.assertTrue((self.dir / "inbox/20261027-090000-aaaa.md").exists())

    def test_rejects_invalid_or_foreign_tasks(self):
        self.capture("20261027-090000-aaaa")
        self.commit_all()
        self.run_cmd("snapshot")
        self.write("tasks/20261027-090000-ffff-1.md", task("20261027-090000-ffff-1"))
        self.write("tasks/20261027-090000-aaaa-1.md", task("20261027-090000-aaaa-1", categories=["Rocketry"]))
        r = self.run_cmd("finalize")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not in this run's snapshot", r.stdout)
        self.assertIn("not in taxonomy.yml", r.stdout)

    def test_validate_reports_problems(self):
        self.write("tasks/20261027-090000-aaaa-1.md", task("20261027-090000-aaaa-1", estimated_effort="3h"))
        r = self.run_cmd("validate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("estimated_effort", r.stdout)


if __name__ == "__main__":
    unittest.main()
