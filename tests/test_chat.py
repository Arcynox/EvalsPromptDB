import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from promptdb import ChatMessage, PromptDB


class ChatPromptTests(unittest.TestCase):
    def test_chat_preserves_order_literals_and_variables_through_storage_and_export(self):
        messages = [ChatMessage("system", "Reply in {{\tlanguage }}"),
                    {"role": "user", "content": "Question: {{pregunta}} ({{ language }})"}]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "prompts.db"
            with PromptDB(path) as db:
                plain = db.save("qa", "Question: {{pregunta}}")
                chat = db.save("qa", messages=(message for message in messages), tags=["prod"])
                self.assertEqual(plain.kind, "text")
                self.assertEqual(plain.render_messages({"pregunta": "Sí"}), [{"role": "user", "content": "Question: Sí"}])

            question = 'Una "pregunta"\n{{language}}'
            inputs = {"pregunta": question, "language": "español"}
            expected = [{"role": "system", "content": "Reply in español"},
                        {"role": "user", "content": f"Question: {question} (español)"}]
            with PromptDB(path, read_only=True) as db:
                saved = db.get("qa", tag="prod")
                self.assertEqual(saved, chat)
                self.assertEqual(saved.kind, "chat")
                self.assertEqual(saved.variables, ("language", "pregunta"))
                self.assertEqual(saved.render_messages(inputs), expected)
                self.assertEqual(json.loads(saved.render(inputs)), expected)
                self.assertIn('"role": "system"', db.compare("qa").versions[1].diff)
                snapshot = db.export_json()
                self.assertIsInstance(json.loads(snapshot)["prompts"][1]["template"], list)

            with PromptDB(":memory:") as restored:
                restored.import_json(snapshot)
                self.assertEqual(restored.get("qa", tag="prod"), chat)
                self.assertEqual(restored.export_json(), snapshot)

    def test_invalid_chat_templates_never_allocate_a_version(self):
        with PromptDB(":memory:") as db:
            invalid = ([], [{"role": "tool", "content": "data"}], [{"role": "user", "content": ["x"]}],
                       [{"role": "user"}], {ChatMessage("user", "unordered")})
            for messages in invalid:
                with self.subTest(messages=messages), self.assertRaises(ValueError):
                    db.save("qa", messages=messages)
            with self.assertRaisesRegex(ValueError, "text template or chat messages"):
                db.save("qa", "old", messages=[ChatMessage("user", "new")])
            self.assertEqual(db.save("qa", "valid").version, 1)

    def test_message_generator_preserves_roles_and_runs_after_all_inputs_are_rendered(self):
        with PromptDB(":memory:") as db:
            db.save("qa", messages=[ChatMessage("system", "Reply with the answer"), ChatMessage("user", "{{task}}")])
            db.add_test("qa", input={"task": "2+2"}, expected="4")
            generator = Mock(return_value="4")
            [run] = db.run_tests("qa", model="demo", generate_messages=generator)
            self.assertEqual(run.score, 1)
            generator.assert_called_once_with([
                {"role": "system", "content": "Reply with the answer"}, {"role": "user", "content": "2+2"},
            ])
            db.add_test("qa", input={}, expected="4")
            generator.reset_mock()
            with self.assertRaisesRegex(KeyError, "task"):
                db.run_tests("qa", model="demo", generate_messages=generator)
            generator.assert_not_called()
            self.assertEqual(db.list_evals("qa"), [run])
