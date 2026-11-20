import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock

from promptdb import PromptDB
from promptdb.scorers import json_match


class AsyncEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_chat_scoring_and_case_selection(self):
        with PromptDB(":memory:") as db:
            db.save("city", messages=[{"role": "user", "content": "{{name}}"}], tags=["baseline"])
            db.save("city", "Latest template {{name}}")
            madrid = db.add_test("city", input={"name": "Madrid"}, expected={"city": "Madrid"})
            cordoba = db.add_test("city", input={"name": "Córdoba"}, expected={"city": "Córdoba"})
            archived = db.add_test("city", input={"name": "old"}, expected={"city": "old"})
            db.set_test_archived("city", archived.id)
            provider = AsyncMock(side_effect=['{"city":"Córdoba"}', '{"city":"Madrid"}'])

            async def scorer(output, expected):
                await asyncio.sleep(0)
                return json_match(output, expected)

            runs = await db.arun_tests("city", tag="baseline", model="async-demo", run_id="group",
                                       test_ids=[cordoba.id, madrid.id, cordoba.id],
                                       generate_messages=provider, scorer=scorer)
            self.assertEqual([run.test_id for run in runs], [cordoba.id, madrid.id])
            self.assertEqual([run.score for run in runs], [1, 1])
            self.assertTrue(all(run.version == 1 and run.run_id == "group" for run in runs))
            self.assertEqual(provider.await_args_list[0].args[0], [{"role": "user", "content": "Córdoba"}])

            provider.reset_mock()
            with self.assertRaisesRegex(KeyError, "No active test case"):
                await db.arun_tests("city", model="async-demo", test_ids=[archived.id],
                                    generate_messages=provider, scorer=scorer)
            provider.assert_not_awaited()

    async def test_cancelled_evaluation_keeps_no_partial_results_or_sqlite_lock(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "async.db"
            with PromptDB(path) as db:
                db.save("echo", "{{word}}")
                db.add_test("echo", input={"word": "one"}, expected="ONE")
                db.add_test("echo", input={"word": "two"}, expected="TWO")
                started = asyncio.Event()
                hold = asyncio.Event()

                async def generate(text):
                    if text == "two":
                        started.set()
                        await hold.wait()
                    return text.upper()

                task = asyncio.create_task(db.arun_tests("echo", model="demo", generate=generate))
                try:
                    await asyncio.wait_for(started.wait(), 3)
                    self.assertEqual(db.list_evals("echo"), [])
                    with PromptDB(path, create=False, timeout=0) as writer:
                        writer.save("echo", "Another version {{word}}")
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

                self.assertEqual(db.list_evals("echo"), [])

    async def test_sync_and_async_apis_validate_requested_cases_before_generation(self):
        with PromptDB(":memory:") as db:
            db.save("prompt", "{{word}}")
            valid = db.add_test("prompt", input={"word": "one"}, expected="ONE")
            db.add_test("prompt", input={}, expected="missing variable")
            generator = Mock(side_effect=str.upper)
            [run] = db.run_tests("prompt", model="demo", generate=generator, test_ids=[valid.id])
            self.assertEqual(run.score, 1)
            generator.assert_called_once_with("one")

            async_generator = AsyncMock(side_effect=str.upper)
            with self.assertRaisesRegex(KeyError, "word"):
                await db.arun_tests("prompt", model="async-demo", generate=async_generator)
            async_generator.assert_not_awaited()
            self.assertEqual(db.list_evals("prompt"), [run])
