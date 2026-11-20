import unittest

from promptdb import PromptDB
from promptdb.scorers import json_match, text_match


class ScoringTests(unittest.TestCase):
    def test_text_scoring_preserves_internal_whitespace_and_case(self):
        self.assertEqual(text_match("  Córdoba \n", "Córdoba"), 1)
        self.assertEqual(text_match("córdoba", "Córdoba"), 0)
        self.assertEqual(text_match("Buenos  días", "Buenos días"), 0)
        with self.assertRaises(ValueError):
            text_match("{}", {})

    def test_json_scoring_preserves_ordered_arrays_numbers_and_booleans(self):
        self.assertEqual(json_match('\ufeff {"b":[1.0,true], "a":null}', {"a": None, "b": [1, True]}), 1)
        self.assertEqual(json_match('{"large":9007199254740993}', {"large": 9007199254740993}), 1)
        self.assertEqual(json_match('{"large":9007199254740992}', {"large": 9007199254740993}), 0)
        self.assertEqual(json_match('{"values":[true,2]}', {"values": [1, 2]}), 0)
        self.assertEqual(json_match('["b","a"]', ["a", "b"]), 0)

    def test_malformed_json_outputs_are_saved_with_a_zero_score(self):
        with PromptDB(":memory:") as db:
            db.save("answer", "{{statement}}")
            db.add_test("answer", input={"statement": "Madrid"}, expected={"city": "Madrid"})
            for output in ('{"city":"Paris","city":"Madrid"}', '{"city":NaN}', '```json\n{}\n```'):
                with self.subTest(output=output):
                    [record] = db.run_tests("answer", model="demo", generate=lambda _: output, scorer=json_match)
                    self.assertEqual(record.score, 0.0)
                    self.assertEqual(record.output, output)
