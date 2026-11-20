import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from promptdb import PromptDB
from promptdb.schema import SCHEMA_VERSION


class QualityTests(unittest.TestCase):
    def test_coverage_and_pairing_use_the_filtered_distinct_cases(self):
        with PromptDB(":memory:") as db:
            db.save("test", "{{text}}")
            db.save("test", "{{ text }}")
            alpha, beta, gamma = [
                db.add_test("test", input={"text": text}, expected=text.upper())
                for text in ("alpha", "beta", "gamma")
            ]
            for version, case, score in ((1, alpha, 1.0), (1, beta, 0.5), (2, alpha, 0.9),
                                         (2, alpha, 0.9), (2, alpha, 0.9), (2, beta, 0.3)):
                db.add_eval("test", version=version, test_id=case.id, score=score, model="local", run_id="current")
            db.add_eval("test", version=2, test_id=gamma.id, score=1, model="local", run_id="previous")
            db.add_eval("test", version=2, test_id=gamma.id, score=1, model="other", run_id="current")
            db.add_eval("test", version=2, score=1, model="local", run_id="current")

            report = db.compare("test", [1, 2], model="local", run_id="current")
            candidate = report.versions[-1]
            self.assertEqual(report.eligible_test_count, 3)
            self.assertEqual([row.test_id for row in candidate.case_scores], [alpha.id, beta.id])
            self.assertEqual(candidate.case_scores[0].evaluation_count, 3)
            self.assertEqual(candidate.evaluation_count, 5)
            self.assertAlmostEqual(candidate.coverage, 2 / 3)
            self.assertAlmostEqual(candidate.average_score, 0.8)
            self.assertAlmostEqual(candidate.case_average_score, 0.6)
            self.assertAlmostEqual(candidate.paired.coverage, 2 / 3)
            self.assertAlmostEqual(candidate.paired.baseline_score, 0.75)
            self.assertAlmostEqual(candidate.paired.candidate_score, 0.6)
            self.assertAlmostEqual(candidate.paired.delta, -0.15)
            self.assertEqual(len(db.list_evals("test", run_id="current", model="local")), 7)

            one = db.compare("test", [1, 2], model="local", run_id="current", test_id=beta.id)
            self.assertEqual(one.eligible_test_count, 1)
            self.assertEqual(one.versions[-1].coverage, 1.0)
            self.assertEqual(one.versions[-1].paired.coverage, 1.0)

    def test_migration_preserves_evaluations_and_is_safe_to_open_concurrently(self):
        fixture = Path(__file__).with_name("fixtures") / "schema-v1.sql"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            with closing(sqlite3.connect(path)) as raw, raw:
                raw.executescript(fixture.read_text(encoding="utf-8"))
                raw.execute("INSERT INTO prompt_names(name, last_version) VALUES ('test', 1)")
                raw.execute("""
                    INSERT INTO prompts VALUES ('test', 1, 'Hi', '[]', NULL, NULL, '{}', '2026-05-01T00:00:00+00:00')
                """)
                raw.execute("""
                    INSERT INTO evaluations(name, version, score, model, metadata, created_at)
                    VALUES ('test', 1, 0.75, 'legacy', '{}', '2026-05-01T00:00:00+00:00')
                """)

            with self.assertRaisesRegex(ValueError, "writable connection"):
                PromptDB(path, read_only=True)
            with closing(sqlite3.connect(path)) as raw:
                self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], 1)

            def read(_):
                with PromptDB(path, create=False) as db:
                    return db.list_evals("test")

            with ThreadPoolExecutor(max_workers=4) as pool:
                copies = list(pool.map(read, range(8)))
            for records in copies:
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].score, .75)
                self.assertIsNone(records[0].run_id)
            with PromptDB(path, create=False) as db:
                run = db.add_eval("test", version=1, score=1, model="local", run_id="new")
                self.assertEqual(run.id, 2)
                self.assertEqual(db.list_evals("test", run_id="new"), [run])
            with closing(sqlite3.connect(path)) as raw:
                self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
                self.assertEqual(raw.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_failed_migration_leaves_the_old_schema_intact(self):
        fixture = Path(__file__).with_name("fixtures") / "schema-v1.sql"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            with closing(sqlite3.connect(path)) as raw, raw:
                raw.executescript(fixture.read_text(encoding="utf-8"))
                raw.execute("CREATE INDEX evaluations_by_run ON evaluations(model)")
            with self.assertRaises(sqlite3.OperationalError):
                PromptDB(path, create=False)
            with closing(sqlite3.connect(path)) as raw:
                self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertNotIn("run_id", [row[1] for row in raw.execute("PRAGMA table_info(evaluations)")])
