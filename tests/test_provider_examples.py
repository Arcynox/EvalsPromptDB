import json
import unittest
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from examples.evaluate import main, make_generator
from promptdb import PromptDB


class ProviderExampleTests(unittest.TestCase):
    def test_local_example_runs_versions_under_one_id(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "evals.db"
            with PromptDB(path) as db:
                db.save("word", "{{word}}")
                db.save("word", "{{ word }}")
                db.add_test("word", input={"word": "córdoba"}, expected="CÓRDOBA")

            with patch("sys.stdout", new_callable=StringIO) as stdout:
                self.assertEqual(main(["local", "word", "1", "2", "--db", str(path)]), 0)
            summary = json.loads(stdout.getvalue())
            self.assertTrue(summary["run_id"])
            self.assertEqual(summary["model"], "local/uppercase")
            with PromptDB(path, create=False) as db:
                scores = db.list_evals("word", run_id=summary["run_id"])
                self.assertEqual([row.output for row in scores], ["CÓRDOBA", "CÓRDOBA"])
                self.assertEqual([row.score for row in scores], [1.0, 1.0])

    def test_ollama_request_shape_and_incomplete_response_rollback(self):
        responses = [BytesIO(b'{"done":true,"message":{"content":"ONE"}}'), BytesIO(b'{"done":false}')]
        with patch("examples.evaluate.urlopen", side_effect=responses) as request, PromptDB(":memory:") as db:
            db.save("word", "Say {{word}}")
            db.add_test("word", input={"word": "one"}, expected="ONE")
            db.add_test("word", input={"word": "two"}, expected="TWO")
            ollama = make_generator("ollama", "local-model", ollama_host="localhost:11434", timeout=7,
                                    ollama_context=512, max_tokens=32)
            with self.assertRaisesRegex(ValueError, "completed chat response"):
                db.run_tests("word", model="local-model", generate=ollama)
            self.assertEqual(db.list_evals("word"), [])

        first = request.call_args_list[0]
        self.assertEqual(first.kwargs, {"timeout": 7})
        self.assertEqual(first.args[0].full_url, "http://localhost:11434/api/chat")
        self.assertEqual(first.args[0].get_method(), "POST")
        payload = json.loads(first.args[0].data)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "Say one"}])
        self.assertEqual(payload["model"], "local-model")
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["options"], {"temperature": 0, "num_ctx": 512, "num_predict": 32})

    def test_openai_and_anthropic_adapters_have_matching_case_results(self):
        responses = Mock(return_value=SimpleNamespace(output_text="Madrid"))
        messages = Mock(return_value=SimpleNamespace(content=[
            SimpleNamespace(type="text", text="Mad"), SimpleNamespace(type="thinking"),
            SimpleNamespace(type="text", text="rid"),
        ]))
        openai = ModuleType("openai")
        openai.OpenAI = Mock(return_value=SimpleNamespace(responses=SimpleNamespace(create=responses)))
        anthropic = ModuleType("anthropic")
        anthropic.Anthropic = Mock(return_value=SimpleNamespace(messages=SimpleNamespace(create=messages)))
        with patch.dict("sys.modules", {"openai": openai, "anthropic": anthropic}), PromptDB(":memory:") as db:
            db.save("city", "Name {{city}}")
            case = db.add_test("city", input={"city": "Madrid"}, expected="Madrid")
            for provider in ("openai", "anthropic"):
                generate = make_generator(provider, "example-model", timeout=7)
                [run] = db.run_tests("city", model=provider + "/example-model", generate=generate, run_id="smoke")
                self.assertEqual((run.test_id, run.score, run.output), (case.id, 1, "Madrid"))
        responses.assert_called_once_with(model="example-model", input="Name Madrid", store=False, max_output_tokens=256)
        messages.assert_called_once_with(model="example-model", max_tokens=256,
                                         messages=[{"role": "user", "content": "Name Madrid"}])
