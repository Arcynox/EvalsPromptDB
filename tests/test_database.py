import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from promptdb import PromptDB


class PromptDBTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "prompts # %.db"

    def test_versions_render_and_persist(self):
        with PromptDB(self.path) as db:
            first = db.save(
                "resumen", "{{tema}} / {{ título }} / {{tema}}", version=1,
                model="local", temperature=0.3, tags=["produccion", "texto", "texto"],
                metadata={"equipo": "edición"},
            )
            third = db.save("resumen", "Resume {{tema}}", version=3, tags=["texto", "experimento"])
            self.assertEqual(first.variables, ("tema", "título"))
            self.assertEqual(first.render({"tema": "{{título}}", "título": "ñ"}), "{{título}} / ñ / {{título}}")
            with self.assertRaisesRegex(KeyError, "título"):
                first.render({"tema": "Córdoba"})
            with self.assertRaisesRegex(ValueError, "placeholders"):
                db.save("resumen", "{{tema}}", variables=["otra"])
            with self.assertRaisesRegex(ValueError, "greater than 3"):
                db.save("resumen", "Duplicado", version=3)
            self.assertEqual(db.list(tags=["texto", "experimento"]), [third])
            self.assertEqual(db.get("resumen", tag="produccion"), first)
            with self.assertRaises(KeyError):
                db.get("resumen", version=3, tag="produccion")

        with PromptDB(self.path, create=False) as reopened:
            self.assertEqual(reopened.history("resumen"), [first, third])
            self.assertEqual(reopened.get("resumen"), third)
            reopened.delete("resumen", 3)
            self.assertEqual(reopened.save("resumen", "Nuevo texto").version, 4)

    def test_promotion_rolls_back_on_failure_and_can_restore_an_older_version(self):
        with PromptDB(self.path) as db:
            first = db.save("revision", "Viejo", tags=["produccion", "auditoría"])
            db.save("revision", "Nuevo", tags=["experimento"])
            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute("""
                    CREATE TRIGGER fail_promotion BEFORE INSERT ON tags
                    WHEN NEW.tag = 'produccion' AND NEW.version = 2
                    BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END
                """)

            with self.assertRaises(sqlite3.IntegrityError):
                db.promote("revision", 2)
            self.assertEqual(db.get("revision", tag="produccion"), first)

            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute("DROP TRIGGER fail_promotion")
            db.promote("revision", 2)
            self.assertEqual(db.get("revision", 1).tags, ("auditoría",))
            self.assertEqual(db.get("revision", tag="produccion").version, 2)
            self.assertEqual(db.promote("revision", 1), first)
            self.assertEqual(db.list(tags=["produccion"]), [first])

    def test_comparison_filters_dates_model_and_test(self):
        start = datetime(2026, 11, 12, 4, tzinfo=timezone(timedelta(hours=-3)))
        end = start + timedelta(days=1)
        with PromptDB(self.path) as db:
            db.save("resumen", "{{tema}} / {{titulo}}")
            db.save("resumen", "{{tema}} / {{fuente}}\n")
            db.save("otro", "Un texto cualquiera")
            case = db.add_test("resumen", input={"tema": "Córdoba"}, expected={"texto": "Resumen"})
            unrelated = db.add_test("otro", input={}, expected="Otro texto")
            with self.assertRaisesRegex(KeyError, "No test case"):
                db.add_eval("resumen", version=1, score=1, model="local", test_id=unrelated.id)
            under_test = db.add_eval("resumen", version=1, score=.4, model="local",
                                     test_id=case.id, created_at=start)
            db.add_eval("resumen", version=2, score=.8, model="local", test_id=case.id, created_at=start)
            db.add_eval("resumen", version=2, score=.6, model="local", test_id=case.id, created_at=start)
            db.add_eval("resumen", version=1, score=1, model="otro", test_id=case.id, created_at=start)
            db.add_eval("resumen", version=1, score=1, model="local", test_id=case.id, created_at=end)
            db.add_eval("resumen", version=1, score=1, model="local", created_at=start)

            filters = {"model": "local", "test_id": case.id, "since": start, "until": end}
            report = db.compare("resumen", [1, 2], **filters)
            self.assertEqual(report.tests, (case,))
            self.assertEqual(report.best.prompt.version, 2)
            self.assertAlmostEqual(report.best.average_score, .7)
            self.assertEqual(report.best.evaluation_count, 2)
            self.assertEqual(report.best.added_variables, ("fuente",))
            self.assertEqual(report.best.removed_variables, ("titulo",))
            self.assertIn("\\ No newline at end of file", report.best.diff)
            self.assertEqual(under_test.created_at.isoformat(), "2026-11-12T07:00:00+00:00")
            self.assertEqual(db.list_evals("resumen", version=1, **filters), [under_test])
            self.assertIsNone(db.compare("resumen", model="desconocido").best)

            db.delete("resumen", 1)
            self.assertEqual(db.list_evals("resumen", version=1), [])
            self.assertEqual(db.list_tests("resumen"), [case])

    def test_concurrent_saves_allocate_distinct_versions(self):
        def save(index):
            with PromptDB(self.path, timeout=60) as db:
                return db.save("concurrente", f"Prompt {index}").version

        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(sorted(pool.map(save, range(24))), list(range(1, 25)))
        with PromptDB(self.path, create=False) as db:
            self.assertEqual([prompt.version for prompt in db.history("concurrente")], list(range(1, 25)))

    def test_open_existing_does_not_create_or_initialize_files(self):
        with self.assertRaises(sqlite3.OperationalError):
            PromptDB(self.path, create=False)
        self.assertFalse(self.path.exists())
        self.path.touch()
        with self.assertRaisesRegex(ValueError, "not a PromptDB database"):
            PromptDB(self.path, create=False)
        self.assertEqual(self.path.stat().st_size, 0)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        with self.assertRaisesRegex(ValueError, "different application"):
            PromptDB(self.path)

    def test_an_existing_database_can_be_read_while_a_writer_holds_a_reservation(self):
        with PromptDB(self.path) as db:
            saved = db.save("saludo", "Hola {{nombre}}")
        with closing(sqlite3.connect(self.path, isolation_level=None)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            with PromptDB(self.path, create=False, timeout=0) as db:
                self.assertEqual(db.get("saludo"), saved)
                with self.assertRaises(sqlite3.OperationalError):
                    db.save("saludo", "Otra versión")

    def test_read_only_rejects_writes_before_calling_a_generator(self):
        with self.assertRaises(sqlite3.OperationalError):
            PromptDB(self.path, read_only=True)
        self.assertFalse(self.path.exists())
        with PromptDB(self.path) as db:
            prompt = db.save("saludo", "Hola {{nombre}}")
            db.add_test("saludo", input={"nombre": "Luigi"}, expected="Hola Luigi")

        with PromptDB(self.path, read_only=True) as db:
            self.assertEqual(db.get("saludo"), prompt)
            self.assertEqual(db.check(), ())
            generator = Mock(return_value="Hola Luigi")
            with self.assertRaisesRegex(PermissionError, "read-only"):
                db.run_tests("saludo", model="demo", generate=generator)
            generator.assert_not_called()
            with self.assertRaises(PermissionError):
                db.delete("saludo", 1)
            destination = self.path.with_name("copy.db")
            db.backup(destination)
            with PromptDB(destination, read_only=True) as copied:
                self.assertEqual(copied.get("saludo"), prompt)

    def test_integrity_reports_orphans_and_backup_refuses_an_existing_sidecar(self):
        with PromptDB(self.path) as db:
            self.assertEqual(db.check(), ())
            destination = self.path.with_name("dest.db")
            sidecar = destination.with_suffix(".db-wal")
            sidecar.write_bytes(b"existing WAL")
            with self.assertRaisesRegex(FileExistsError, "sidecar"):
                db.backup(destination)
            self.assertEqual(sidecar.read_bytes(), b"existing WAL")
            self.assertFalse(destination.exists())

        with closing(sqlite3.connect(self.path)) as raw, raw:
            raw.execute("PRAGMA foreign_keys = OFF")
            raw.execute("INSERT INTO test_cases(name, input, expected, created_at) VALUES (?, ?, ?, ?)",
                        ("missing", "{}", '"expected"', "2026-01-01T00:00:00+00:00"))
        with PromptDB(self.path, read_only=True) as db:
            self.assertEqual(len(db.check()), 1)
            self.assertIn("test_cases row 1", db.check()[0])

    def test_backup_copies_wal_changes_and_never_overwrites(self):
        destination = self.path.with_name("backup.db")
        with PromptDB(self.path) as db, closing(sqlite3.connect(self.path)) as writer:
            self.assertEqual(writer.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")
            db.save("resumen", "{{texto}}", tags=["produccion"])
            db.save("resumen", "Resume {{texto}}")
            case = db.add_test("resumen", input={"texto": "Córdoba"}, expected="Resumen")
            run = db.add_eval("resumen", version=2, score=.8, model="local", test_id=case.id)

            self.assertEqual(db.backup(destination), destination)
            with self.assertRaises(FileExistsError):
                db.backup(self.path)
            db.save("resumen", "Solo en el original")
            with self.assertRaises(FileExistsError):
                db.backup(destination)

        with closing(sqlite3.connect(destination)) as check:
            self.assertEqual(check.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(check.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        with PromptDB(destination, create=False) as restored:
            self.assertEqual(restored.get("resumen").version, 2)
            self.assertEqual(restored.get("resumen", tag="produccion").version, 1)
            self.assertEqual(restored.list_tests("resumen"), [case])
            self.assertEqual(restored.list_evals("resumen"), [run])

    def test_run_tests_records_each_case_and_supports_a_custom_scorer(self):
        with PromptDB(self.path) as db:
            first = db.save("echo", "{{city}}")
            db.save("echo", "Input: {{city}}")
            madrid = db.add_test("echo", input={"city": "Madrid"}, expected="Madrid")
            cordoba = db.add_test("echo", input={"city": "Córdoba"}, expected="Córdoba")
            runs = db.run_tests("echo", version=first.version, model="local", generate=lambda text: text + "\n")
            self.assertEqual([run.test_id for run in runs], [madrid.id, cordoba.id])
            self.assertEqual([run.score for run in runs], [1.0, 1.0])
            self.assertEqual({run.created_at for run in runs}, {runs[0].created_at})
            self.assertTrue(runs[0].run_id)
            self.assertEqual({run.run_id for run in runs}, {runs[0].run_id})
            self.assertEqual(db.list_evals("echo", version=first.version), runs)

            db.save("json", "{{city}}")
            db.add_test("json", input={"city": "Córdoba"}, expected={"city": "Córdoba"})
            with self.assertRaisesRegex(ValueError, "custom scorer"):
                db.run_tests("json", model="local", generate=str)
            [run] = db.run_tests("json", model="local", generate=str,
                                 scorer=lambda text, expected: float(text == expected["city"]))
            self.assertEqual(run.score, 1.0)

    def test_run_tests_leaves_no_partial_scores_if_generation_or_writes_fail(self):
        with PromptDB(self.path) as db:
            db.save("echo", "{{value}}")
            db.add_test("echo", input={"value": "first"}, expected="first")
            second = db.add_test("echo", input={"value": "second"}, expected="second")

            def generate(text):
                if text == "second":
                    raise RuntimeError("model failed")
                return text

            with self.assertRaisesRegex(RuntimeError, "model failed"):
                db.run_tests("echo", model="test", generate=generate)
            self.assertEqual(db.list_evals("echo"), [])

            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute(f"""
                    CREATE TRIGGER reject_second_case BEFORE INSERT ON evaluations
                    WHEN NEW.test_id = {second.id}
                    BEGIN SELECT RAISE(ABORT, 'simulated database failure'); END
                """)
            with self.assertRaises(sqlite3.IntegrityError):
                db.run_tests("echo", model="test", generate=str)
            self.assertEqual(db.list_evals("echo"), [])
