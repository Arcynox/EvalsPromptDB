import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from promptdb import __version__


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "cli # %.db"

    def cli(self, *args, stdin=None, status=0):
        result = subprocess.run(
            [sys.executable, "-m", "promptdb", "--db", str(self.database), *args],
            capture_output=True, input=stdin, encoding="utf-8", timeout=15, check=False,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        self.assertEqual(result.returncode, status, result.stderr)
        return result

    def test_cli_flow_from_stdin_to_comparison_and_backup(self):
        self.assertEqual(self.cli("--version").stdout.strip(), f"promptdb {__version__}")
        self.assertEqual(
            self.cli("save", "resumen", "--template", "Resume {{texto}}", "--tag", "produccion").stdout.strip(),
            "resumen@1",
        )
        self.cli("save", "resumen", "--from-file", "-", stdin="Resume {{texto}} en una frase. {{firma}}")
        self.assertEqual(
            self.cli("get", "resumen", "--tag", "produccion", "--input", '{"texto":"Córdoba"}').stdout,
            "Resume Córdoba",
        )
        case = json.loads(self.cli(
            "add-test", "resumen", "--input", '{"texto":"Córdoba"}', "--expected", '"Una ciudad"',
        ).stdout)
        self.cli("eval", "resumen", "--version", "2", "--score", "0.87", "--model", "local",
                 "--test-id", str(case["id"]), "--created-at", "2026-01-01T00:00:00Z")

        filters = ("--model", "local", "--since", "2026-01-01T00:00:00Z", "--until", "2026-01-02T00:00:00Z")
        table = self.cli("compare", "resumen", "1", "2", *filters).stdout
        self.assertIn("0.870", table)
        self.assertIn("--- resumen@1", table)
        report = json.loads(self.cli("compare", "resumen", "--json", *filters).stdout)
        self.assertEqual(report["tests"], [case])
        self.assertEqual(report["versions"][1]["added_variables"], ["firma"])

        self.cli("tag", "resumen", "2", "--tag", "experimento")
        self.assertEqual(json.loads(self.cli("list", "--tag", "experimento").stdout)[0]["version"], 2)
        self.cli("tag", "resumen", "2", "--clear")
        self.assertEqual(json.loads(self.cli("list", "--tag", "experimento").stdout), [])
        self.cli("promote", "resumen", "2")
        self.cli("promote", "resumen", "1")
        destination = self.database.with_name("snapshot.db")
        self.cli("backup", str(destination))
        self.cli("delete", "resumen", "2")
        self.assertEqual(json.loads(self.cli("evals", "resumen").stdout), [])
        self.database = destination
        self.assertEqual(len(json.loads(self.cli("evals", "resumen").stdout)), 1)
        self.assertEqual(json.loads(self.cli("get", "resumen", "--tag", "produccion", "--json").stdout)["version"], 1)

    def test_read_and_backup_require_an_existing_database(self):
        destination = self.database.with_name("snapshot.db")
        for args in (("list",), ("get", "faltante"), ("backup", str(destination))):
            with self.subTest(args=args):
                failure = self.cli(*args, status=1)
                self.assertIn("promptdb:", failure.stderr)
                self.assertFalse(self.database.exists())
                self.assertFalse(destination.exists())

    def test_from_file_preserves_template_and_render_errors_are_readable(self):
        source = self.database.with_name("plantilla.txt")
        self.cli("save", "saludo", "--from-file", str(source), status=1)
        self.assertFalse(self.database.exists())
        template = "Hola, {{nombre}}\r\nHasta pronto."
        source.write_bytes(template.encode("utf-8"))
        self.cli("save", "saludo", "--from-file", str(source))
        self.assertEqual(json.loads(self.cli("get", "saludo", "--json").stdout)["template"], template)
        self.assertIn("Missing prompt variables: nombre", self.cli("get", "saludo", "--input", "{}", status=1).stderr)

    def test_snapshot_round_trip_and_quality_threshold(self):
        self.cli("save", "saludo", "--template", "Hola {{lugar}}")
        self.cli("save", "saludo", "--template", "Córdoba {{lugar}}")
        no_evals = self.cli("compare", "saludo", "1", "2", "--min-score", "0.9", status=3)
        self.assertIn("no matching evaluations", no_evals.stderr)
        self.cli("eval", "saludo", "--version", "2", "--model", "local", "--score", "0.8")
        failed = self.cli("compare", "saludo", "1", "2", "--min-score", "0.9", "--json", status=3)
        self.assertIn("is below", failed.stderr)
        self.assertEqual(json.loads(failed.stdout)["versions"][1]["average_score"], .8)
        self.cli("compare", "saludo", "1", "2", "--min-score", "0.8")
        self.assertIn("between 0 and 1", self.cli("compare", "saludo", "--min-score", "nan", status=1).stderr)

        payload = self.cli("export").stdout
        self.assertIn("Córdoba", payload)
        self.database = self.database.with_name("restore.db")
        self.cli("import", "-", stdin=payload)
        self.assertEqual(self.cli("export").stdout, payload)
        self.cli("import", "-", stdin=payload, status=1)
