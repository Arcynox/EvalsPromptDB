import json
import sqlite3
from datetime import datetime
from typing import Any

from ._values import (
    chat_messages,
    chat_template,
    evaluation_output,
    json_object,
    json_value,
    label,
    message_variables,
    number,
    positive_integer,
    template_variables,
    timestamp,
)

FORMAT = "evals-promptdb"
FORMAT_VERSION = 4

# Foreign-key dependencies determine import order. Each order-by key is unique.
TABLES = (
    ("prompt_names", "name last_version", "name"),
    ("prompts", "name version template variables model temperature metadata created_at kind", "name, version"),
    ("tags", "name version tag", "name, version, tag"),
    ("test_cases", "id name input expected created_at archived_at", "id"),
    ("evaluations", "id name version score model test_id output metadata created_at run_id", "id"),
)
JSON_COLUMNS = {
    "prompts": ("variables", "metadata"),
    "test_cases": ("input", "expected"),
    "evaluations": ("metadata",),
}


def encode(connection: sqlite3.Connection) -> str:
    sequence = connection.execute("SELECT seq FROM sqlite_sequence WHERE name = 'evaluations'").fetchone()
    snapshot: dict[str, Any] = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "last_evaluation_id": 0 if sequence is None else sequence[0],
    }
    for table, columns, order in TABLES:
        selection = ", ".join(columns.split())
        records = []
        for row in connection.execute(f"SELECT {selection} FROM {table} ORDER BY {order}"):
            record = dict(row)
            for column in JSON_COLUMNS.get(table, ()):
                record[column] = json.loads(record[column])
            if table == "prompts" and record["kind"] == "chat":
                record["template"] = json.loads(record["template"])
            records.append(record)
        snapshot[table] = records
    return json.dumps(snapshot, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n"


def _read_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(items)
    if len(result) != len(items):
        raise ValueError("Duplicate field in snapshot JSON")
    return result


def decode(payload: str) -> dict[str, Any]:
    snapshot = json.loads(payload, object_pairs_hook=_read_object)
    keys = {"format", "version", "last_evaluation_id", *(table for table, _, _ in TABLES)}
    if not isinstance(snapshot, dict) or snapshot.keys() != keys:
        raise ValueError("Invalid snapshot structure")
    version = snapshot["version"]
    if snapshot["format"] != FORMAT or type(version) is not int or not 1 <= version <= FORMAT_VERSION:
        raise ValueError("Unsupported snapshot format or version")
    last_eval = snapshot["last_evaluation_id"]
    if type(last_eval) is not int or not 0 <= last_eval <= 2**63 - 1:
        raise ValueError("last_evaluation_id must be a non-negative 64-bit integer")
    return snapshot


def _row_values(table: str, columns: list[str], record: Any) -> tuple[Any, ...]:
    if not isinstance(record, dict) or record.keys() != set(columns):
        raise ValueError("Unexpected record fields")
    row = dict(record)
    label(row["name"], "name")
    if "version" in row:
        positive_integer(row["version"], "version")
    if "id" in row:
        positive_integer(row["id"], "id")
    if "created_at" in row:
        row["created_at"] = timestamp(datetime.fromisoformat(row["created_at"]))
    if "metadata" in row:
        row["metadata"] = json_object(row["metadata"], "metadata")

    match table:
        case "prompt_names":
            positive_integer(row["last_version"], "last_version")
        case "prompts":
            kind = row.get("kind", "text")
            if kind == "chat":
                if not isinstance(row["template"], list):
                    raise ValueError("A chat template must be a JSON array")
                messages = chat_messages(row["template"])
                detected = message_variables(messages)
                row["template"] = chat_template(messages)
            elif kind == "text":
                detected = template_variables(row["template"])
            else:
                raise ValueError("Prompt kind must be text or chat")
            if row["variables"] != list(detected):
                raise ValueError("variables must match the detected variables in order")
            row["variables"] = json_value(detected)
            if row["model"] is not None:
                label(row["model"], "model")
            if row["temperature"] is not None:
                row["temperature"] = number(row["temperature"], 2, "temperature")
        case "tags":
            label(row["tag"], "tag")
        case "test_cases":
            row["input"] = json_object(row["input"], "input")
            row["expected"] = json_value(row["expected"])
            if row.get("archived_at") is not None:
                row["archived_at"] = timestamp(datetime.fromisoformat(row["archived_at"]))
        case "evaluations":
            row["score"] = number(row["score"], 1, "score")
            label(row["model"], "model")
            if row["test_id"] is not None:
                positive_integer(row["test_id"], "test_id")
            if row.get("run_id") is not None:
                label(row["run_id"], "run_id")
            evaluation_output(row["output"])

    return tuple(row[column] for column in columns)


def restore(connection: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
    if connection.execute("SELECT 1 FROM prompt_names LIMIT 1").fetchone() is not None:
        raise ValueError("Import requires a new or unused PromptDB database")
    for table, fields, _ in TABLES:
        records = snapshot[table]
        if not isinstance(records, list):
            raise ValueError(f"{table} must be an array")
        columns = fields.split()
        if table == "prompts" and snapshot["version"] < 3:
            columns.remove("kind")
        if table == "test_cases" and snapshot["version"] < 4:
            columns.remove("archived_at")
        if table == "evaluations" and snapshot["version"] == 1:
            columns.remove("run_id")
        names = ", ".join(columns)
        parameters = ", ".join("?" for _ in columns)
        insert = f"INSERT INTO {table} ({names}) VALUES ({parameters})"
        for index, record in enumerate(records, start=1):
            try:
                connection.execute(insert, _row_values(table, columns, record))
            except (TypeError, ValueError, OverflowError, sqlite3.IntegrityError) as exc:
                raise ValueError(f"Invalid {table} record {index}: {exc}") from exc

    if connection.execute("""
        SELECT 1 FROM prompts p JOIN prompt_names n USING (name) WHERE p.version > n.last_version LIMIT 1
    """).fetchone() is not None:
        raise ValueError("last_version cannot be less than a saved version")

    maximum_id = connection.execute("SELECT COALESCE(MAX(id), 0) FROM evaluations").fetchone()[0]
    last_eval = snapshot["last_evaluation_id"]
    if last_eval < maximum_id:
        raise ValueError("last_evaluation_id cannot be less than a saved evaluation ID")
    if last_eval:
        connection.execute("DELETE FROM sqlite_sequence WHERE name = 'evaluations'")
        connection.execute("INSERT INTO sqlite_sequence(name, seq) VALUES ('evaluations', ?)", (last_eval,))
