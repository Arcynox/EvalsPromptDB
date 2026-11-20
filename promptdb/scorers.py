"""Standard-library scorers for model outputs."""

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from ._values import json_value, unique_json_object

__all__ = ["json_match", "text_match"]


def text_match(output: str, expected: str) -> float:
    """Score an exact text match after trimming surrounding whitespace."""
    if not isinstance(expected, str):
        raise ValueError("Text scoring requires a string expectation")
    return float(output.strip() == expected.strip())


def _reject_constant(token: str) -> None:
    raise ValueError(f"Invalid JSON number {token}")


def _parse_json(text: str) -> Any:
    return json.loads(text.removeprefix("\ufeff"), parse_int=Decimal, parse_float=Decimal,
                      parse_constant=_reject_constant, object_pairs_hook=unique_json_object)


def _same_value(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(_same_value(value, expected[key]) for key, value in actual.items())
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(_same_value(a, b) for a, b in zip(actual, expected))
    return bool(actual == expected)


def json_match(output: str, expected: Any) -> float:
    """Score structural JSON equality. Invalid model output scores zero."""
    fixture = _parse_json(json_value(expected))
    try:
        return float(_same_value(_parse_json(output), fixture))
    except (ValueError, InvalidOperation, RecursionError):
        return 0.0
