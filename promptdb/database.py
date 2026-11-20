from __future__ import annotations

import difflib
import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime
from io import StringIO
from math import fsum
from os import PathLike, link
from os.path import lexists
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal
from uuid import uuid4

from . import _snapshots
from ._values import (
    chat_messages,
    chat_template,
    evaluation_output,
    json_object,
    json_value,
    label,
    labels,
    message_variables,
    number,
    positive_integer,
    template_variables,
    timestamp,
)
from .records import (
    CaseScore,
    ChatMessage,
    Comparison,
    Evaluation,
    PairedScore,
    Prompt,
    TestCase,
    VersionComparison,
)
from .schema import APPLICATION_ID, MIGRATIONS, SCHEMA, SCHEMA_VERSION


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


def _paired_score(
    baseline: dict[int, CaseScore], candidate: dict[int, CaseScore], eligible_test_count: int,
) -> PairedScore | None:
    shared = baseline.keys() & candidate.keys()
    if not shared:
        return None
    count = len(shared)
    before = fsum(baseline[test_id].average_score for test_id in shared) / count
    after = fsum(candidate[test_id].average_score for test_id in shared) / count
    return PairedScore(count, count / eligible_test_count, before, after, after - before)


class PromptDB:
    """An embedded SQLite store. Open a separate instance per thread."""

    def __init__(
        self, path: str | PathLike[str] = "prompts.db", *, create: bool = True, timeout: float = 15.0,
        read_only: bool = False,
    ) -> None:
        self._read_only = read_only
        uri = read_only or not create
        if uri:
            mode = "ro" if read_only else "rw"
            database = Path(path).absolute().as_uri() + f"?mode={mode}"
        else:
            database = path
        self._conn = _connect(database, uri=uri, timeout=timeout)
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

    def _require_writable(self) -> None:
        if self._read_only:
            raise PermissionError("The database is open in read-only mode")

    @contextmanager
    def _transaction(self, *, write: bool = False) -> Iterator[None]:
        if write:
            self._require_writable()
        self._conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self._conn.execute("COMMIT")
        except BaseException:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def _schema_version(self) -> int:
        application_id = self._conn.execute("PRAGMA application_id").fetchone()[0]
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if application_id == APPLICATION_ID:
            if not 1 <= version <= SCHEMA_VERSION:
                raise ValueError(f"Unsupported PromptDB schema version: {version}")
            return version
        if application_id or version:
            raise ValueError("The SQLite file belongs to a different application")
        return 0

    def _initialize(self, create: bool) -> None:
        with self._transaction():
            version = self._schema_version()
            if version == SCHEMA_VERSION:
                return
        if not version and (not create or self._read_only):
            raise ValueError("The SQLite file is not a PromptDB database")
        if self._read_only:
            raise ValueError(f"Schema {version} needs a writable connection to migrate to {SCHEMA_VERSION}")

        with self._transaction(write=True):
            version = self._schema_version()
            if version == SCHEMA_VERSION:
                return
            if version:
                for previous in range(version, SCHEMA_VERSION):
                    for statement in MIGRATIONS[previous]:
                        self._conn.execute(statement)
            else:
                existing_schema = self._conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
                if existing_schema is not None:
                    raise ValueError("The SQLite file belongs to a different application")
                for statement in SCHEMA:
                    self._conn.execute(statement)
                self._conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def check(self) -> tuple[str, ...]:
        """Report SQLite integrity and foreign-key violations in one read snapshot."""
        with self._transaction():
            issues = [message for (message,) in self._conn.execute("PRAGMA integrity_check") if message != "ok"]
            for table, row_id, parent, key in self._conn.execute("PRAGMA foreign_key_check"):
                issues.append(f"{table} row {row_id}: foreign key {key} references missing row in {parent}")
        return tuple(issues)

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
            for suffix in ("-journal", "-wal", "-shm"):
                sidecar = str(destination) + suffix
                if lexists(sidecar):
                    raise FileExistsError(f"SQLite sidecar exists at the backup destination: {sidecar}")
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
        self, name: str, template: str | None = None, *,
        messages: Iterable[ChatMessage | Mapping[str, str]] | None = None, version: int | None = None,
        variables: Iterable[str] | None = None, model: str | None = None,
        temperature: float | None = None, tags: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> Prompt:
        """Save a new version of a text or chat prompt."""
        label(name, "name")
        kind = "text"
        if messages is not None:
            if template is not None:
                raise ValueError("Provide a text template or chat messages")
            ordered = chat_messages(messages)
            template = chat_template(ordered)
            detected = message_variables(ordered, variables)
            kind = "chat"
        else:
            if template is None:
                raise ValueError("A text template or chat messages are required")
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
                   (name, version, template, variables, model, temperature, metadata, created_at, kind)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, version, template, json_value(detected), model, temperature, metadata_json,
                 timestamp(), kind),
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
            messages=chat_messages(json.loads(row["template"])) if row["kind"] == "chat" else (),
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
            return self._insert_test(name, values, expected_json, timestamp())

    def _insert_test(self, name: str, values: str, expected_json: str, created: str) -> TestCase:
        cursor = self._conn.execute(
            "INSERT INTO test_cases(name, input, expected, created_at) VALUES (?, ?, ?, ?)",
            (name, values, expected_json, created),
        )
        return self.get_test(name, cursor.lastrowid)

    @staticmethod
    def _test_case(row: sqlite3.Row) -> TestCase:
        archived = row["archived_at"]
        return TestCase(row["id"], row["name"], json.loads(row["input"]), json.loads(row["expected"]),
                        datetime.fromisoformat(row["created_at"]), datetime.fromisoformat(archived) if archived else None)

    def get_test(self, name: str, test_id: int) -> TestCase:
        """Get an active or archived case by ID and prompt name."""
        row = self._conn.execute(
            "SELECT * FROM test_cases WHERE name = ? AND id = ?",
            (label(name, "name"), positive_integer(test_id, "test_id")),
        ).fetchone()
        if row is None:
            raise KeyError(f"No test case {test_id} for prompt {name!r}")
        return self._test_case(row)

    def list_tests(self, name: str, *, include_archived: bool = False) -> list[TestCase]:
        """List active cases or include archived cases for an audit."""
        active = "" if include_archived else " AND archived_at IS NULL"
        rows = self._conn.execute(
            f"SELECT * FROM test_cases WHERE name = ?{active} ORDER BY id", (label(name, "name"),)
        )
        return [self._test_case(row) for row in rows]

    def set_test_archived(self, name: str, test_id: int, *, archived: bool = True) -> TestCase:
        """Archive or restore a case without changing its contents or scores."""
        with self._transaction(write=True):
            case = self.get_test(name, test_id)
            if archived == (case.archived_at is not None):
                return case
            changed_at = timestamp() if archived else None
            self._conn.execute("UPDATE test_cases SET archived_at = ? WHERE id = ?", (changed_at, case.id))
            return replace(case, archived_at=datetime.fromisoformat(changed_at) if changed_at else None)

    def replace_test(self, name: str, test_id: int, *, input: Mapping[str, Any], expected: Any) -> TestCase:
        """Archive an active case and create its replacement in one transaction."""
        values = json_object(input, "input")
        expected_json = json_value(expected)
        with self._transaction(write=True):
            case = self.get_test(name, test_id)
            if case.archived_at is not None:
                raise ValueError(f"Test case {test_id} is already archived")
            changed_at = timestamp()
            self._conn.execute("UPDATE test_cases SET archived_at = ? WHERE id = ?", (changed_at, case.id))
            return self._insert_test(name, values, expected_json, changed_at)

    def add_eval(
        self, name: str, *, version: int, score: float, model: str, test_id: int | None = None,
        output: str | None = None, metadata: Mapping[str, Any] | None = None,
        created_at: datetime | None = None, run_id: str | None = None,
    ) -> Evaluation:
        """Record a score between 0 and 1, optionally linked to a test case."""
        label(name, "name")
        positive_integer(version, "version")
        score = number(score, 1, "score")
        label(model, "model")
        if run_id is not None:
            label(run_id, "run_id")
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
                                     timestamp(created_at), run_id)

    def _insert_eval(
        self, name: str, version: int, score: float, model: str, test_id: int | None,
        output: str | None, metadata: str, created_at: str, run_id: str | None,
    ) -> Evaluation:
        cursor = self._conn.execute(
            """INSERT INTO evaluations
               (name, version, score, model, test_id, output, metadata, created_at, run_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, version, score, model, test_id, output, metadata, created_at, run_id),
        )
        return Evaluation(cursor.lastrowid, name, version, score, model, test_id, output,
                          json.loads(metadata), datetime.fromisoformat(created_at), run_id)

    def run_tests(
        self, name: str, *, model: str, generate: Callable[[str], str] | None = None,
        generate_messages: Callable[[list[dict[str, str]]], str] | None = None,
        version: int | Literal["latest"] = "latest", scorer: Callable[[str, Any], float] | None = None,
        metadata: Mapping[str, Any] | None = None, run_id: str | None = None,
    ) -> list[Evaluation]:
        """Run cases with a text or chat generator and record the results as a batch."""
        self._require_writable()
        if (generate is None) == (generate_messages is None):
            raise ValueError("Provide exactly one of generate and generate_messages")
        label(model, "model")
        run_id = uuid4().hex if run_id is None else label(run_id, "run_id")
        metadata_json = json_object({} if metadata is None else metadata, "metadata")
        with self._transaction():
            prompt = self._get(label(name, "name"), version)
            tests = self.list_tests(name)
        if not tests:
            return []
        if scorer is None and any(not isinstance(test.expected, str) for test in tests):
            raise ValueError("A custom scorer is required for non-text expectations")

        # Render all cases before invoking code that may call an external provider.
        if generate_messages is not None:
            rendered_messages = [prompt.render_messages(test.input) for test in tests]
            outputs = (generate_messages(messages) for messages in rendered_messages)
        else:
            assert generate is not None
            rendered = [prompt.render(test.input) for test in tests]
            outputs = (generate(text) for text in rendered)
        results = []
        for test, output in zip(tests, outputs):
            if not isinstance(output, str):
                raise ValueError("generate must return a string")
            score = float(output.strip() == test.expected.strip()) if scorer is None else scorer(output, test.expected)
            results.append((test.id, output, number(score, 1, "score")))

        created = timestamp()
        with self._transaction(write=True):
            self._get(name, prompt.version)
            return [
                self._insert_eval(name, prompt.version, score, model, test_id, output, metadata_json, created, run_id)
                for test_id, output, score in results
            ]

    @staticmethod
    def _eval_filter(
        name: str, version: int | None, model: str | None, since: datetime | None,
        until: datetime | None, test_id: int | None, run_id: str | None,
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
        if run_id is not None:
            conditions.append("run_id = ?")
            params.append(label(run_id, "run_id"))
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
        run_id: str | None = None,
    ) -> list[Evaluation]:
        """List runs with optional filters, using the time interval [since, until)."""
        where, params = self._eval_filter(name, version, model, since, until, test_id, run_id)
        rows = self._conn.execute(
            f"SELECT * FROM evaluations WHERE {where} ORDER BY created_at, id", params
        )
        return [Evaluation(
            id=row["id"], name=row["name"], version=row["version"], score=row["score"],
            model=row["model"], test_id=row["test_id"], output=row["output"],
            metadata=json.loads(row["metadata"]), created_at=datetime.fromisoformat(row["created_at"]),
            run_id=row["run_id"],
        ) for row in rows]

    def compare(
        self, name: str, versions: Iterable[int] | None = None, *, model: str | None = None,
        since: datetime | None = None, until: datetime | None = None, test_id: int | None = None,
        run_id: str | None = None, include_archived: bool = False,
    ) -> Comparison:
        """Compare templates against the first requested version and summarize stored scores."""
        where, params = self._eval_filter(name, None, model, since, until, test_id, run_id)
        if not include_archived:
            where += """ AND NOT EXISTS (SELECT 1 FROM test_cases c
                              WHERE c.id = evaluations.test_id AND c.archived_at IS NOT NULL)"""
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
            tests = tuple(self.list_tests(name, include_archived=include_archived))
            eligible_test_count = len(tests) if test_id is None else int(any(test.id == test_id for test in tests))
            statistics = {
                version: (count, average) for version, count, average in self._conn.execute(
                    f"SELECT version, COUNT(*), AVG(score) FROM evaluations WHERE {where} GROUP BY version",
                    params,
                )
            }
            by_case: dict[int, dict[int, CaseScore]] = {}
            for version, case_id, count, average in self._conn.execute(
                f"""SELECT version, test_id, COUNT(*), AVG(score) FROM evaluations
                    WHERE {where} AND test_id IS NOT NULL
                    GROUP BY version, test_id ORDER BY version, test_id""", params,
            ):
                by_case.setdefault(version, {})[case_id] = CaseScore(case_id, count, average)

            baseline = by_case.get(base.version, {})
            versions_compared = []
            for prompt in prompts:
                count, average = statistics.get(prompt.version, (0, None))
                scores = by_case.get(prompt.version, {})
                case_average = fsum(row.average_score for row in scores.values()) / len(scores) if scores else None
                paired = None
                if prompt.version != base.version:
                    paired = _paired_score(baseline, scores, eligible_test_count)
                versions_compared.append(VersionComparison(
                    prompt=prompt, diff=_diff(base, prompt),
                    added_variables=tuple(var for var in prompt.variables if var not in base.variables),
                    removed_variables=tuple(var for var in base.variables if var not in prompt.variables),
                    evaluation_count=count, average_score=average,
                    case_scores=tuple(scores.values()),
                    coverage=len(scores) / eligible_test_count if eligible_test_count else None,
                    paired=paired,
                    case_average_score=case_average,
                ))
            return Comparison(name, base.version, tuple(versions_compared), tests, eligible_test_count)
