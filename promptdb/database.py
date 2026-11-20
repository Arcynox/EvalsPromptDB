from __future__ import annotations

import difflib
import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import datetime
from io import StringIO
from os import PathLike, link
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from . import _snapshots
from ._values import (
    evaluation_output,
    json_object,
    json_value,
    label,
    labels,
    number,
    positive_integer,
    template_variables,
    timestamp,
)
from .records import Comparison, Evaluation, Prompt, TestCase, VersionComparison
from .schema import APPLICATION_ID, SCHEMA, SCHEMA_VERSION


def _connect(path: str | PathLike[str], *, uri: bool = False, timeout: float = 15.0) -> sqlite3.Connection:
    options: dict[str, Any] = {"isolation_level": None, "uri": uri, "timeout": timeout}
    if hasattr(sqlite3, "LEGACY_TRANSACTION_CONTROL"):
        options["autocommit"] = True
    return sqlite3.connect(path, **options)


def _diff(base: Prompt, target: Prompt) -> str:
    lines = difflib.unified_diff(
        StringIO(base.template).readlines(), StringIO(target.template).readlines(),
        fromfile=f"{base.name}@{base.version}", tofile=f"{target.name}@{target.version}",
    )
    return "".join(
        line if line.endswith("\n") else f"{line}\n\\ No newline at end of file\n"
        for line in lines
    )


class PromptDB:
    """An embedded SQLite store. Open a separate instance per thread."""

    def __init__(
        self, path: str | PathLike[str] = "prompts.db", *, create: bool = True, timeout: float = 15.0,
    ) -> None:
        if create:
            database = path
        else:
            database = Path(path).absolute().as_uri() + "?mode=rw"
        self._conn = _connect(database, uri=not create, timeout=timeout)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._initialize(create)
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> PromptDB:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[None]:
        self._conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self._conn.execute("COMMIT")
        except BaseException:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def _has_schema(self) -> bool:
        application_id = self._conn.execute("PRAGMA application_id").fetchone()[0]
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if application_id == APPLICATION_ID:
            if version != SCHEMA_VERSION:
                raise ValueError(f"Unsupported PromptDB schema version: {version}")
            return True
        if application_id or version:
            raise ValueError("The SQLite file belongs to a different application")
        return False

    def _initialize(self, create: bool) -> None:
        with self._transaction():
            if self._has_schema():
                return
        if not create:
            raise ValueError("The SQLite file is not a PromptDB database")

        with self._transaction(write=True):
            if self._has_schema():
                return
            existing_schema = self._conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            if existing_schema is not None:
                raise ValueError("The SQLite file belongs to a different application")
            for statement in SCHEMA:
                self._conn.execute(statement)
            self._conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def backup(self, destination: str | PathLike[str]) -> Path:
        """Write a standalone SQLite snapshot to a new file without overwriting it."""
        destination = Path(destination).absolute()
        with TemporaryDirectory(prefix=".promptdb-backup-", dir=destination.parent) as staging:
            snapshot = Path(staging) / "snapshot.db"
            snapshot.touch(mode=0o600, exist_ok=False)
            with closing(_connect(snapshot)) as target:
                with self._transaction():
                    # Acquire a read snapshot under SQLite's normal busy timeout.
                    self._conn.execute("PRAGMA schema_version").fetchone()
                    self._conn.backup(target)
                target.execute("PRAGMA journal_mode = DELETE").fetchone()
            link(snapshot, destination)
        return destination

    def export_json(self) -> str:
        """Return a deterministic JSON snapshot of the full database."""
        with self._transaction():
            return _snapshots.encode(self._conn)

    def import_json(self, payload: str) -> None:
        """Restore a JSON snapshot transactionally into a new or unused database."""
        snapshot = _snapshots.decode(payload)
        with self._transaction(write=True):
            _snapshots.restore(self._conn, snapshot)

    def save(
        self, name: str, template: str, *, version: int | None = None,
        variables: Iterable[str] | None = None, model: str | None = None,
        temperature: float | None = None, tags: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> Prompt:
        """Save a new version. Version numbers increase per name and are never reused."""
        label(name, "name")
        detected = template_variables(template, variables)
        tags = labels(tags, "tags")
        if model is not None:
            label(model, "model")
        if temperature is not None:
            temperature = number(temperature, 2, "temperature")
        metadata_json = json_object({} if metadata is None else metadata, "metadata")

        with self._transaction(write=True):
            self._conn.execute("INSERT OR IGNORE INTO prompt_names(name) VALUES (?)", (name,))
            last = self._conn.execute(
                "SELECT last_version FROM prompt_names WHERE name = ?", (name,)
            ).fetchone()[0]
            version = positive_integer(last + 1 if version is None else version, "version")
            if version <= last:
                raise ValueError(f"version must be greater than {last} for {name!r}")
            self._conn.execute(
                """INSERT INTO prompts
                   (name, version, template, variables, model, temperature, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, version, template, json_value(detected), model, temperature, metadata_json,
                 timestamp()),
            )
            self._conn.execute(
                "UPDATE prompt_names SET last_version = ? WHERE name = ?", (version, name)
            )
            self._insert_tags(name, version, tags)
            return self._get(name, version)

    def _insert_tags(self, name: str, version: int, tags: Iterable[str]) -> None:
        self._conn.executemany(
            "INSERT INTO tags(name, version, tag) VALUES (?, ?, ?)",
            ((name, version, tag) for tag in tags),
        )

    def _prompt(self, row: sqlite3.Row) -> Prompt:
        tags = self._conn.execute(
            "SELECT tag FROM tags WHERE name = ? AND version = ? ORDER BY tag",
            (row["name"], row["version"]),
        )
        return Prompt(
            name=row["name"], version=row["version"], template=row["template"],
            variables=tuple(json.loads(row["variables"])), model=row["model"],
            temperature=row["temperature"], metadata=json.loads(row["metadata"]),
            tags=tuple(tag[0] for tag in tags), created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _get(self, name: str, version: int | Literal["latest"], tag: str | None = None) -> Prompt:
        where = ["p.name = ?"]
        params: list[str | int] = [name]
        if version != "latest":
            where.append("p.version = ?")
            params.append(positive_integer(version, "version"))
        if tag is not None:
            where.append(
                "EXISTS (SELECT 1 FROM tags WHERE name = p.name AND version = p.version AND tag = ?)"
            )
            params.append(label(tag, "tag"))
        row = self._conn.execute(
            f"SELECT p.* FROM prompts p WHERE {' AND '.join(where)} ORDER BY p.version DESC LIMIT 1",
            params,
        ).fetchone()
        if row is None:
            raise KeyError(f"No prompt {name!r} with version={version!r}, tag={tag!r}")
        return self._prompt(row)

    def get(
        self, name: str, version: int | Literal["latest"] = "latest", *, tag: str | None = None,
    ) -> Prompt:
        """Get a particular version or the highest version matching the optional tag."""
        with self._transaction():
            return self._get(label(name, "name"), version, tag)

    def list(self, *, tags: Iterable[str] = ()) -> list[Prompt]:
        """List all versions with every requested tag, ordered by name and version."""
        tags = labels(tags, "tags")
        query = "SELECT * FROM prompts"
        if tags:
            placeholders = ", ".join("?" for _ in tags)
            query += f""" WHERE (name, version) IN (
                SELECT name, version FROM tags WHERE tag IN ({placeholders})
                GROUP BY name, version HAVING COUNT(*) = ?
            )"""
        params = (*tags, len(tags)) if tags else ()
        with self._transaction():
            return [self._prompt(row) for row in self._conn.execute(query + " ORDER BY name, version", params)]

    def history(self, name: str) -> list[Prompt]:
        """List saved versions of a prompt from oldest to newest."""
        with self._transaction():
            rows = self._conn.execute(
                "SELECT * FROM prompts WHERE name = ? ORDER BY version", (label(name, "name"),)
            )
            return [self._prompt(row) for row in rows]

    def set_tags(self, name: str, version: int, tags: Iterable[str]) -> Prompt:
        """Replace the tags on an existing version."""
        tags = labels(tags, "tags")
        positive_integer(version, "version")
        with self._transaction(write=True):
            self._get(label(name, "name"), version)
            self._conn.execute("DELETE FROM tags WHERE name = ? AND version = ?", (name, version))
            self._insert_tags(name, version, tags)
            return self._get(name, version)

    def promote(self, name: str, version: int, *, tag: str = "produccion") -> Prompt:
        """Move a tag to exactly one version of this prompt in a single transaction."""
        label(tag, "tag")
        positive_integer(version, "version")
        with self._transaction(write=True):
            self._get(label(name, "name"), version)
            self._conn.execute("DELETE FROM tags WHERE name = ? AND tag = ?", (name, tag))
            self._insert_tags(name, version, (tag,))
            return self._get(name, version)

    def delete(self, name: str, version: int) -> None:
        """Delete one version and its evaluations. Shared test cases survive."""
        with self._transaction(write=True):
            result = self._conn.execute(
                "DELETE FROM prompts WHERE name = ? AND version = ?",
                (label(name, "name"), positive_integer(version, "version")),
            )
            if result.rowcount == 0:
                raise KeyError(f"No prompt {name!r} with version={version}")

    def add_test(self, name: str, *, input: Mapping[str, Any], expected: Any) -> TestCase:
        """Add a JSON fixture shared by all versions of a prompt."""
        values = json_object(input, "input")
        expected_json = json_value(expected)
        with self._transaction(write=True):
            self._get(label(name, "name"), "latest")
            created = timestamp()
            cursor = self._conn.execute(
                "INSERT INTO test_cases(name, input, expected, created_at) VALUES (?, ?, ?, ?)",
                (name, values, expected_json, created),
            )
            return TestCase(cursor.lastrowid, name, json.loads(values), json.loads(expected_json),
                            datetime.fromisoformat(created))

    def list_tests(self, name: str) -> list[TestCase]:
        rows = self._conn.execute(
            "SELECT * FROM test_cases WHERE name = ? ORDER BY id", (label(name, "name"),)
        )
        return [TestCase(row["id"], row["name"], json.loads(row["input"]), json.loads(row["expected"]),
                         datetime.fromisoformat(row["created_at"])) for row in rows]

    def add_eval(
        self, name: str, *, version: int, score: float, model: str, test_id: int | None = None,
        output: str | None = None, metadata: Mapping[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> Evaluation:
        """Record a score between 0 and 1, optionally linked to a test case."""
        label(name, "name")
        positive_integer(version, "version")
        score = number(score, 1, "score")
        label(model, "model")
        evaluation_output(output)
        metadata_json = json_object({} if metadata is None else metadata, "metadata")
        with self._transaction(write=True):
            self._get(name, version)
            if test_id is not None:
                positive_integer(test_id, "test_id")
                case = self._conn.execute(
                    "SELECT id FROM test_cases WHERE name = ? AND id = ?", (name, test_id)
                ).fetchone()
                if case is None:
                    raise KeyError(f"No test case {test_id} for prompt {name!r}")
            return self._insert_eval(name, version, score, model, test_id, output, metadata_json,
                                     timestamp(created_at))

    def _insert_eval(
        self, name: str, version: int, score: float, model: str, test_id: int | None,
        output: str | None, metadata: str, created_at: str,
    ) -> Evaluation:
        cursor = self._conn.execute(
            """INSERT INTO evaluations
               (name, version, score, model, test_id, output, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, version, score, model, test_id, output, metadata, created_at),
        )
        return Evaluation(cursor.lastrowid, name, version, score, model, test_id, output,
                          json.loads(metadata), datetime.fromisoformat(created_at))

    def run_tests(
        self, name: str, *, model: str, generate: Callable[[str], str],
        version: int | Literal["latest"] = "latest", scorer: Callable[[str, Any], float] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> list[Evaluation]:
        """Run all saved cases through a caller-supplied generator and record them as a batch."""
        label(model, "model")
        metadata_json = json_object({} if metadata is None else metadata, "metadata")
        with self._transaction():
            prompt = self._get(label(name, "name"), version)
            tests = self.list_tests(name)
        if not tests:
            return []
        if scorer is None and any(not isinstance(test.expected, str) for test in tests):
            raise ValueError("A custom scorer is required for non-text expectations")

        # Render all cases before invoking code that may call an external provider.
        rendered = [prompt.render(test.input) for test in tests]
        results = []
        for test, text in zip(tests, rendered):
            output = generate(text)
            if not isinstance(output, str):
                raise ValueError("generate must return a string")
            score = float(output.strip() == test.expected.strip()) if scorer is None else scorer(output, test.expected)
            results.append((test.id, output, number(score, 1, "score")))

        created = timestamp()
        with self._transaction(write=True):
            self._get(name, prompt.version)
            return [
                self._insert_eval(name, prompt.version, score, model, test_id, output, metadata_json, created)
                for test_id, output, score in results
            ]

    @staticmethod
    def _eval_filter(
        name: str, version: int | None, model: str | None, since: datetime | None,
        until: datetime | None, test_id: int | None,
    ) -> tuple[str, list[Any]]:
        conditions = ["name = ?"]
        params: list[Any] = [label(name, "name")]
        if version is not None:
            conditions.append("version = ?")
            params.append(positive_integer(version, "version"))
        if model is not None:
            conditions.append("model = ?")
            params.append(label(model, "model"))
        if test_id is not None:
            conditions.append("test_id = ?")
            params.append(positive_integer(test_id, "test_id"))
        start = timestamp(since) if since is not None else None
        end = timestamp(until) if until is not None else None
        if start is not None and end is not None and start >= end:
            raise ValueError("since must be earlier than until")
        if start is not None:
            conditions.append("created_at >= ?")
            params.append(start)
        if end is not None:
            conditions.append("created_at < ?")
            params.append(end)
        return " AND ".join(conditions), params

    def list_evals(
        self, name: str, *, version: int | None = None, model: str | None = None,
        since: datetime | None = None, until: datetime | None = None, test_id: int | None = None,
    ) -> list[Evaluation]:
        """List runs with optional filters, using the time interval [since, until)."""
        where, params = self._eval_filter(name, version, model, since, until, test_id)
        rows = self._conn.execute(
            f"SELECT * FROM evaluations WHERE {where} ORDER BY created_at, id", params
        )
        return [Evaluation(
            id=row["id"], name=row["name"], version=row["version"], score=row["score"],
            model=row["model"], test_id=row["test_id"], output=row["output"],
            metadata=json.loads(row["metadata"]), created_at=datetime.fromisoformat(row["created_at"]),
        ) for row in rows]

    def compare(
        self, name: str, versions: Iterable[int] | None = None, *, model: str | None = None,
        since: datetime | None = None, until: datetime | None = None, test_id: int | None = None,
    ) -> Comparison:
        """Compare templates against the first requested version and summarize stored scores."""
        where, params = self._eval_filter(name, None, model, since, until, test_id)
        with self._transaction():
            if versions is None:
                versions = (row[0] for row in self._conn.execute(
                    "SELECT version FROM prompts WHERE name = ? ORDER BY version", (name,)
                ))
            prompts = [self._get(name, version) for version in dict.fromkeys(
                positive_integer(version, "version") for version in versions
            )]
            if not prompts:
                raise ValueError(f"No versions to compare for {name!r}")
            base = prompts[0]
            statistics = {
                version: (count, average) for version, count, average in self._conn.execute(
                    f"SELECT version, COUNT(*), AVG(score) FROM evaluations WHERE {where} GROUP BY version",
                    params,
                )
            }
            versions_compared = []
            for prompt in prompts:
                count, average = statistics.get(prompt.version, (0, None))
                versions_compared.append(VersionComparison(
                    prompt=prompt, diff=_diff(base, prompt),
                    added_variables=tuple(var for var in prompt.variables if var not in base.variables),
                    removed_variables=tuple(var for var in base.variables if var not in prompt.variables),
                    evaluation_count=count, average_score=average,
                ))
            return Comparison(name, base.version, tuple(versions_compared), tuple(self.list_tests(name)))
