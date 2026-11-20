import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from promptdb import PromptDB


class TestCaseLifecycleTests(unittest.TestCase):
    def test_archive_and_replace_preserve_scores_without_changing_active_coverage(self):
        with PromptDB(":memory:") as db:
            db.save("prompt", "{{word}}")
            first = db.add_test("prompt", input={"word": "one"}, expected="ONE")
            evaluation = db.add_eval("prompt", version=1, test_id=first.id, score=1, model="demo")

            second = db.replace_test("prompt", first.id, input={"word": "two"}, expected="TWO")
            self.assertNotEqual(first.id, second.id)
            archived = db.get_test("prompt", first.id)
            self.assertIsNotNone(archived.archived_at)
            self.assertEqual(archived.expected, "ONE")
            self.assertEqual(db.set_test_archived("prompt", first.id), archived)
            self.assertEqual(db.list_tests("prompt"), [second])
            self.assertEqual(db.list_tests("prompt", include_archived=True), [archived, second])
            self.assertEqual(db.list_evals("prompt"), [evaluation])
            with self.assertRaisesRegex(ValueError, "already archived"):
                db.replace_test("prompt", first.id, input={}, expected="ignored")

            current = db.compare("prompt").versions[0]
            self.assertEqual(current.coverage, 0)
            self.assertIsNone(current.average_score)
            audit = db.compare("prompt", include_archived=True, test_id=first.id).versions[0]
            self.assertEqual(audit.coverage, 1)
            self.assertEqual(audit.average_score, 1)
            [run] = db.run_tests("prompt", model="demo", generate=str.upper)
            self.assertEqual((run.test_id, run.score), (second.id, 1))

            snapshot = db.export_json()
            with PromptDB(":memory:") as restored:
                restored.import_json(snapshot)
                self.assertEqual(restored.get_test("prompt", first.id), archived)
                self.assertEqual(restored.list_tests("prompt"), [second])
                self.assertEqual(restored.export_json(), snapshot)
            self.assertEqual(db.set_test_archived("prompt", first.id, archived=False), first)

    def test_replace_rolls_back_archival_when_inserting_the_replacement_fails(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "replacement.db"
            with PromptDB(path) as db:
                db.save("prompt", "{{word}}")
                old = db.add_test("prompt", input={"word": "one"}, expected="ONE")
                with closing(sqlite3.connect(path)) as raw, raw:
                    raw.execute("""
                        CREATE TRIGGER reject_replacement BEFORE INSERT ON test_cases
                        BEGIN SELECT RAISE(ABORT, 'replacement rejected'); END
                    """)
                with self.assertRaisesRegex(sqlite3.IntegrityError, "replacement rejected"):
                    db.replace_test("prompt", old.id, input={"word": "two"}, expected="TWO")
                self.assertEqual(db.list_tests("prompt", include_archived=True), [old])
