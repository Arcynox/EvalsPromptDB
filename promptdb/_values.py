import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from .records import PLACEHOLDER


def label(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{field} must be a non-empty string without null characters")
    return value


def labels(values: Iterable[str], field: str) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError(f"{field} must be a collection of strings")
    return tuple(dict.fromkeys(label(value, field) for value in values))


def positive_integer(value: int, field: str) -> int:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise ValueError(f"{field} must be a positive 64-bit integer")
    return value


def number(value: float, maximum: float, field: str) -> float:
    if type(value) not in (int, float) or not 0 <= value <= maximum:
        raise ValueError(f"{field} must be a number between 0 and {maximum:g}")
    return float(value)


def json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def json_object(value: Mapping[str, Any], field: str) -> str:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be a JSON object with string keys")
    return json_value(dict(value))


def timestamp(value: datetime | None = None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timestamps must be timezone-aware datetimes")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def template_variables(template: str, variables: Iterable[str] | None = None) -> tuple[str, ...]:
    if not isinstance(template, str) or not template.strip():
        raise ValueError("template must be a non-empty string")
    detected = tuple(dict.fromkeys(PLACEHOLDER.findall(template)))
    if variables is not None and set(labels(variables, "variables")) != set(detected):
        raise ValueError("variables must match the placeholders in the template")
    return detected


def evaluation_output(value: str | None) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError("output must be a string")
    return value
