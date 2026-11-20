import json
import unittest
from datetime import datetime, timezone

from promptdb import PromptDB


class SnapshotTests(unittest.TestCase):
    def test_round_trip_preserves_records_and_deleted_version_counters(self):
        with PromptDB(":memory:") as source:
            first = source.save("résumé", "Resume {{título}}", tags=["produccion"], metadata={"b": 1, "a": 2})
            removed = source.save("résumé", "Revisado", version=3)
            case = source.add_test("résumé", input={"título": "Córdoba"}, expected={"output": "Córdoba"})
            run = source.add_eval("résumé", version=1, score=.8, model="local", test_id=case.id,
                                  output="Córdoba", created_at=datetime(2026, 5, 4, tzinfo=timezone.utc))
            deleted_run = source.add_eval("résumé", version=removed.version, score=.5, model="local")
            source.delete("résumé", removed.version)
            snapshot = source.export_json()
            self.assertEqual(snapshot, source.export_json())
            self.assertEqual(json.loads(snapshot)["prompts"][0]["metadata"], {"a": 2, "b": 1})

        with PromptDB(":memory:") as target:
            target.import_json(snapshot)
            self.assertEqual(target.export_json(), snapshot)
            self.assertEqual(target.get("résumé", tag="produccion"), first)
            self.assertEqual(target.list_tests("résumé"), [case])
            self.assertEqual(target.list_evals("résumé"), [run])
            next_prompt = target.save("résumé", "Otro prompt")
            self.assertEqual(next_prompt.version, 4)
            next_run = target.add_eval("résumé", version=4, score=1, model="local")
            self.assertEqual(next_run.id, deleted_run.id + 1)
            with self.assertRaisesRegex(ValueError, "new or unused"):
                target.import_json(snapshot)

    def test_invalid_foreign_keys_or_counters_leave_the_target_untouched(self):
        with PromptDB(":memory:") as source, PromptDB(":memory:") as target:
            source.save("sample", "Hola {{nombre}}", version=2)
            source.add_eval("sample", version=2, score=1, model="local")
            payload = source.export_json()
            original = target.export_json()

            data = json.loads(payload)
            data["evaluations"][0]["test_id"] = 5
            with self.assertRaisesRegex(ValueError, "evaluations record 1"):
                target.import_json(json.dumps(data))
            self.assertEqual(target.export_json(), original)

            data = json.loads(payload)
            data["prompt_names"][0]["last_version"] = 1
            with self.assertRaisesRegex(ValueError, "last_version"):
                target.import_json(json.dumps(data))
            self.assertEqual(target.export_json(), original)

            data = json.loads(payload)
            data["last_evaluation_id"] = 0
            with self.assertRaisesRegex(ValueError, "last_evaluation_id"):
                target.import_json(json.dumps(data))
            self.assertEqual(target.export_json(), original)
            target.import_json(payload)
            self.assertEqual(target.export_json(), payload)

    def test_unsupported_versions_and_duplicate_fields_are_rejected(self):
        with PromptDB(":memory:") as db:
            snapshot = db.export_json()
            future = json.loads(snapshot)
            future["version"] += 1
            with self.assertRaisesRegex(ValueError, "Unsupported snapshot"):
                db.import_json(json.dumps(future))
            with self.assertRaisesRegex(ValueError, "Duplicate field"):
                db.import_json(snapshot.replace('"version": 1', '"version": 1, "version": 1'))
            self.assertEqual(db.export_json(), snapshot)
