import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime
from math import isclose
from os import open as open_fd
from pathlib import Path
from typing import Any, TextIO

from ._version import __version__
from .database import PromptDB
from .records import Comparison
from .scorers import json_match, text_match


def _object_arg(text: str) -> dict[str, Any]:
    result = json.loads(text)
    if not isinstance(result, dict):
        raise argparse.ArgumentTypeError("expected a JSON object")
    return result


def _version_arg(text: str) -> int | str:
    return text if text == "latest" else int(text)


def _date_arg(text: str) -> datetime:
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    date = datetime.fromisoformat(text)
    if date.utcoffset() is None:
        raise argparse.ArgumentTypeError("expected an ISO 8601 timestamp with a timezone")
    return date


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"Cannot serialize {type(value).__name__} to JSON")


def _print_json(value: Any) -> None:
    _write_utf8(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default) + "\n")


def _write_utf8(text: str, stream: TextIO | None = None) -> None:
    stream = sys.stdout if stream is None else stream
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(text)
    else:
        buffer.write(text.encode("utf-8"))


def _read_utf8(source: str) -> str:
    if source == "-":
        buffer = getattr(sys.stdin, "buffer", None)
        return sys.stdin.read() if buffer is None else buffer.read().decode("utf-8")
    with Path(source).open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _export_file(destination: Path, payload: str) -> None:
    target = open(destination, "xb", opener=lambda path, flags: open_fd(path, flags, 0o600))
    try:
        with target:
            target.write(payload.encode("utf-8"))
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _print_comparison(comparison: Comparison) -> None:
    headers = ("Version", "Variables", "Added", "Removed", "Evals", "Avg score", "Cases", "Case avg", "Paired delta")
    rows = [headers]
    for row in comparison.versions:
        rows.append((
            str(row.prompt.version), ", ".join(row.prompt.variables) or "-",
            ", ".join(row.added_variables) or "-", ", ".join(row.removed_variables) or "-",
            str(row.evaluation_count), "-" if row.average_score is None else f"{row.average_score:.3f}",
            f"{len(row.case_scores)}/{comparison.eligible_test_count}",
            "-" if row.case_average_score is None else f"{row.case_average_score:.3f}",
            "-" if row.paired is None else f"{row.paired.delta:+.3f} ({row.paired.case_count})",
        ))
    widths = [max(map(len, column)) for column in zip(*rows)]
    _write_utf8(f"{comparison.name}  (base v{comparison.base_version}, {len(comparison.tests)} test cases)\n")
    for cells in rows:
        _write_utf8("  ".join(value.ljust(width) for value, width in zip(cells, widths)).rstrip() + "\n")
    for row in comparison.versions:
        if row.diff:
            _write_utf8(f"\n{row.diff}")


def _add_eval_filters(command: argparse.ArgumentParser) -> None:
    command.add_argument("--model")
    command.add_argument("--test-id", type=int)
    command.add_argument("--run-id", help="only evaluations recorded under this run ID")
    command.add_argument("--since", type=_date_arg, help="inclusive ISO 8601 timestamp")
    command.add_argument("--until", type=_date_arg, help="exclusive ISO 8601 timestamp")


def _add_test_fields(command: argparse.ArgumentParser) -> None:
    command.add_argument("--input", type=_object_arg, required=True)
    command.add_argument("--expected", type=json.loads, required=True, help="expected output as JSON")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="promptdb", description="Store and compare LLM prompts locally")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--db", type=Path, default="prompts.db", help="SQLite file (default: prompts.db)")
    parser.add_argument("--read-only", action="store_true", help="open an existing database without writes or migrations")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="initialize an empty database")
    commands.add_parser("check", help="verify SQLite integrity and foreign keys")

    save = commands.add_parser("save", help="save a new prompt version")
    save.add_argument("name")
    template = save.add_mutually_exclusive_group(required=True)
    template.add_argument("--template")
    template.add_argument("--from-file", metavar="PATH", help="read UTF-8 text, or use - for stdin")
    template.add_argument("--messages", metavar="PATH", help="read a JSON chat template, or use - for stdin")
    save.add_argument("--version", type=int)
    save.add_argument("--model")
    save.add_argument("--temperature", type=float)
    save.add_argument("--tag", action="append", default=[])
    save.add_argument("--metadata", type=_object_arg)

    get = commands.add_parser("get", help="retrieve a version or render it with JSON inputs")
    get.add_argument("name")
    get.add_argument("--version", type=_version_arg, default="latest")
    get.add_argument("--tag")
    output = get.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--input", type=_object_arg)

    listing = commands.add_parser("list", help="list prompt versions with all requested tags")
    listing.add_argument("--tag", action="append", default=[])
    commands.add_parser("history", help="list versions of a prompt").add_argument("name")

    tag = commands.add_parser("tag", help="replace a version's tags")
    tag.add_argument("name")
    tag.add_argument("version", type=int)
    tags = tag.add_mutually_exclusive_group(required=True)
    tags.add_argument("--tag", action="append", default=[])
    tags.add_argument("--clear", action="store_true", help="remove all tags from this version")

    promote = commands.add_parser("promote", help="move a tag to a specific version")
    promote.add_argument("name")
    promote.add_argument("version", type=int)
    promote.add_argument("--tag", default="produccion")

    delete = commands.add_parser("delete", help="delete a version and its evaluations")
    delete.add_argument("name")
    delete.add_argument("version", type=int)

    backup = commands.add_parser("backup", help="copy this database to a new SQLite file")
    backup.add_argument("destination", type=Path)
    export = commands.add_parser("export", help="write a deterministic UTF-8 JSON snapshot")
    export.add_argument("--output", type=Path, help="write to a new file instead of stdout")
    restore = commands.add_parser("import", help="restore JSON into a new or unused database")
    restore.add_argument("source", help="UTF-8 snapshot path, or - for stdin")

    test = commands.add_parser("add-test", help="add a test case shared across versions")
    test.add_argument("name")
    _add_test_fields(test)
    tests = commands.add_parser("tests", help="list the active test cases")
    tests.add_argument("name")
    tests.add_argument("--include-archived", action="store_true")
    for action, explanation in (
        ("get-test", "read a test case by ID"), ("archive-test", "retire a case and keep its scores"),
        ("restore-test", "make an archived case active"), ("replace-test", "archive a case and add its replacement"),
    ):
        command = commands.add_parser(action, help=explanation)
        command.add_argument("name")
        command.add_argument("test_id", type=int)
        if action == "replace-test":
            _add_test_fields(command)

    evaluate = commands.add_parser("eval", help="record an evaluation score")
    evaluate.add_argument("name")
    evaluate.add_argument("--version", type=int, required=True)
    grading = evaluate.add_mutually_exclusive_group(required=True)
    grading.add_argument("--score", type=float)
    grading.add_argument("--scorer", choices=("text", "json"), help="score the output against the selected case")
    evaluate.add_argument("--model", required=True)
    evaluate.add_argument("--test-id", type=int)
    evaluate.add_argument("--run-id")
    result = evaluate.add_mutually_exclusive_group()
    result.add_argument("--output")
    result.add_argument("--output-file", metavar="PATH", help="read model output in UTF-8 or from stdin with -")
    evaluate.add_argument("--metadata", type=_object_arg)
    evaluate.add_argument("--created-at", type=_date_arg, help="ISO 8601 time of an existing run")

    evaluations = commands.add_parser("evals", help="list stored evaluations")
    evaluations.add_argument("name")
    evaluations.add_argument("--version", type=int)
    _add_eval_filters(evaluations)

    compare = commands.add_parser("compare", help="compare templates, variables and scores")
    compare.add_argument("name")
    compare.add_argument("versions", type=int, nargs="*", help="versions, starting with the base")
    _add_eval_filters(compare)
    compare.add_argument("--json", action="store_true")
    compare.add_argument("--include-archived", action="store_true", help="include archived cases and their scores")
    compare.add_argument("--min-score", type=float, help="exit 3 if the last compared version misses this score")
    compare.add_argument("--min-coverage", type=float, help="required fraction of eligible cases scored")
    compare.add_argument("--max-regression", type=float, help="maximum drop against the base on the same cases")
    return parser


def _check_quality(comparison: Comparison, args: argparse.Namespace) -> int:
    candidate = comparison.versions[-1]
    version = f"v{candidate.prompt.version}"
    errors = []
    if args.min_score is not None:
        track_cases = args.min_coverage is not None or args.max_regression is not None
        average = candidate.case_average_score if track_cases else candidate.average_score
        metric = "case average" if track_cases else "average"
        if average is None:
            missing = "case evaluations" if track_cases else "evaluations"
            errors.append(f"{version} has no matching {missing}")
        elif average < args.min_score:
            errors.append(f"{version} {metric} {average:g} is below {args.min_score:g}")
    if args.min_coverage is not None:
        if candidate.coverage is None:
            errors.append(f"{version} has no eligible test cases")
        elif candidate.coverage < args.min_coverage:
            errors.append(f"{version} coverage {candidate.coverage:.1%} is below {args.min_coverage:.1%}")
    if args.max_regression is not None:
        paired = candidate.paired
        required = 1.0 if args.min_coverage is None else args.min_coverage
        if paired is None:
            errors.append(f"{version} has no scored cases in common with v{comparison.base_version}")
        elif paired.coverage < required:
            errors.append(f"{version} paired coverage {paired.coverage:.1%} is below {required:.1%}")
        elif -paired.delta > args.max_regression and not isclose(
            -paired.delta, args.max_regression, rel_tol=0.0, abs_tol=1e-12,
        ):
            errors.append(f"{version} paired score dropped by {-paired.delta:.3g}, above {args.max_regression:g}")
    for error in errors:
        _write_utf8(f"promptdb: {error}\n", sys.stderr)
    return 3 if errors else 0


def _run(db: PromptDB, args: argparse.Namespace) -> int:
    match args.command:
        case "init":
            return 0
        case "check":
            issues = db.check()
            _print_json({"ok": not issues, "issues": issues})
            return 1 if issues else 0
        case "save":
            prompt = db.save(args.name, args.template, messages=args.chat, version=args.version, model=args.model,
                             temperature=args.temperature, tags=args.tag, metadata=args.metadata)
            _write_utf8(f"{prompt.name}@{prompt.version}\n")
        case "get":
            prompt = db.get(args.name, args.version, tag=args.tag)
            if args.json:
                _print_json(prompt)
            else:
                _write_utf8(prompt.template if args.input is None else prompt.render(args.input))
        case "list":
            _print_json(db.list(tags=args.tag))
        case "history":
            _print_json(db.history(args.name))
        case "tag":
            _print_json(db.set_tags(args.name, args.version, args.tag))
        case "promote":
            _print_json(db.promote(args.name, args.version, tag=args.tag))
        case "delete":
            db.delete(args.name, args.version)
        case "backup":
            _write_utf8(f"{db.backup(args.destination)}\n")
        case "export":
            payload = db.export_json()
            if args.output is None:
                _write_utf8(payload)
            else:
                _export_file(args.output, payload)
        case "import":
            db.import_json(args.payload)
        case "add-test":
            _print_json(db.add_test(args.name, input=args.input, expected=args.expected))
        case "tests":
            _print_json(db.list_tests(args.name, include_archived=args.include_archived))
        case "get-test":
            _print_json(db.get_test(args.name, args.test_id))
        case "archive-test" | "restore-test":
            _print_json(db.set_test_archived(args.name, args.test_id, archived=args.command == "archive-test"))
        case "replace-test":
            _print_json(db.replace_test(args.name, args.test_id, input=args.input, expected=args.expected))
        case "eval":
            score = args.score
            if args.scorer is not None:
                if args.output is None or args.test_id is None:
                    raise ValueError("--scorer needs --test-id and --output or --output-file")
                expected = db.get_test(args.name, args.test_id).expected
                scorer = text_match if args.scorer == "text" else json_match
                score = scorer(args.output, expected)
            _print_json(db.add_eval(args.name, version=args.version, score=score, model=args.model,
                                    test_id=args.test_id, output=args.output, metadata=args.metadata,
                                    created_at=args.created_at, run_id=args.run_id))
        case "evals":
            _print_json(db.list_evals(args.name, version=args.version, model=args.model, test_id=args.test_id,
                                      since=args.since, until=args.until, run_id=args.run_id))
        case "compare":
            for flag, value in (("--min-score", args.min_score), ("--min-coverage", args.min_coverage),
                                ("--max-regression", args.max_regression)):
                if value is not None and not 0 <= value <= 1:
                    raise ValueError(f"{flag} must be between 0 and 1")
            comparison = db.compare(args.name, args.versions or None, model=args.model, test_id=args.test_id,
                                    since=args.since, until=args.until, run_id=args.run_id,
                                    include_archived=args.include_archived)
            if args.max_regression is not None and len(comparison.versions) < 2:
                raise ValueError("--max-regression requires at least two versions")
            if args.json:
                _print_json(comparison)
            else:
                _print_comparison(comparison)
            return _check_quality(comparison, args)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.read_only and args.command == "init":
            raise PermissionError("init requires a writable database")
        if args.command == "save":
            args.chat = None
            if args.from_file is not None:
                args.template = _read_utf8(args.from_file)
            if args.messages is not None:
                args.chat = json.loads(_read_utf8(args.messages).removeprefix("\ufeff"))
                if not isinstance(args.chat, list):
                    raise ValueError("--messages requires a JSON array")
        if args.command == "import":
            args.payload = _read_utf8(args.source).removeprefix("\ufeff")
        if args.command == "eval" and args.output_file is not None:
            args.output = _read_utf8(args.output_file)
        with PromptDB(args.db, create=args.command in {"init", "save", "import"}, read_only=args.read_only) as db:
            return _run(db, args)
    except (KeyError, OSError, ValueError, sqlite3.Error) as error:
        detail = error.args[0] if isinstance(error, KeyError) else str(error)
        _write_utf8(f"promptdb: {detail}\n", sys.stderr)
        return 1
