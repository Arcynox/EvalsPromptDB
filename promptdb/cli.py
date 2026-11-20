import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ._version import __version__
from .database import PromptDB
from .records import Comparison


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
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Cannot serialize {type(value).__name__} to JSON")


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default))


def _print_comparison(comparison: Comparison) -> None:
    headers = ("Version", "Variables", "Added", "Removed", "Evals", "Avg score")
    rows = [headers]
    for row in comparison.versions:
        rows.append((
            str(row.prompt.version), ", ".join(row.prompt.variables) or "-",
            ", ".join(row.added_variables) or "-", ", ".join(row.removed_variables) or "-",
            str(row.evaluation_count), "-" if row.average_score is None else f"{row.average_score:.3f}",
        ))
    widths = [max(map(len, column)) for column in zip(*rows)]
    print(f"{comparison.name}  (base v{comparison.base_version}, {len(comparison.tests)} test cases)")
    for row in rows:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip())
    for row in comparison.versions:
        if row.diff:
            print(f"\n{row.diff}", end="")


def _add_eval_filters(command: argparse.ArgumentParser) -> None:
    command.add_argument("--model")
    command.add_argument("--test-id", type=int)
    command.add_argument("--since", type=_date_arg, help="inclusive ISO 8601 timestamp")
    command.add_argument("--until", type=_date_arg, help="exclusive ISO 8601 timestamp")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="promptdb", description="Store and compare LLM prompts locally")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--db", type=Path, default="prompts.db", help="SQLite file (default: prompts.db)")
    commands = parser.add_subparsers(dest="command", required=True)

    save = commands.add_parser("save", help="save a new prompt version")
    save.add_argument("name")
    template = save.add_mutually_exclusive_group(required=True)
    template.add_argument("--template")
    template.add_argument("--from-file", metavar="PATH", help="read UTF-8 text, or use - for stdin")
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
    commands.add_parser("export", help="write a deterministic JSON snapshot to stdout")
    restore = commands.add_parser("import", help="restore JSON into a new or unused database")
    restore.add_argument("source", help="UTF-8 snapshot path, or - for stdin")

    test = commands.add_parser("add-test", help="add a test case shared across versions")
    test.add_argument("name")
    test.add_argument("--input", type=_object_arg, required=True)
    test.add_argument("--expected", type=json.loads, required=True, help="expected output as JSON")
    commands.add_parser("tests", help="list test cases").add_argument("name")

    evaluate = commands.add_parser("eval", help="record an evaluation score")
    evaluate.add_argument("name")
    evaluate.add_argument("--version", type=int, required=True)
    evaluate.add_argument("--score", type=float, required=True)
    evaluate.add_argument("--model", required=True)
    evaluate.add_argument("--test-id", type=int)
    evaluate.add_argument("--output")
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
    compare.add_argument("--min-score", type=float, help="exit 3 if the last compared version misses this score")
    return parser


def _read_template(args: argparse.Namespace) -> str:
    if args.template is not None:
        return args.template
    if args.from_file == "-":
        return sys.stdin.read()
    with Path(args.from_file).open(encoding="utf-8", newline="") as source:
        return source.read()


def _run(db: PromptDB, args: argparse.Namespace) -> int:
    match args.command:
        case "save":
            prompt = db.save(args.name, args.template, version=args.version, model=args.model,
                             temperature=args.temperature, tags=args.tag, metadata=args.metadata)
            print(f"{prompt.name}@{prompt.version}")
        case "get":
            prompt = db.get(args.name, args.version, tag=args.tag)
            if args.json:
                _print_json(prompt)
            else:
                sys.stdout.write(prompt.template if args.input is None else prompt.render(args.input))
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
            print(db.backup(args.destination))
        case "export":
            sys.stdout.buffer.write(db.export_json().encode("utf-8"))
        case "import":
            db.import_json(args.payload)
        case "add-test":
            _print_json(db.add_test(args.name, input=args.input, expected=args.expected))
        case "tests":
            _print_json(db.list_tests(args.name))
        case "eval":
            _print_json(db.add_eval(args.name, version=args.version, score=args.score, model=args.model,
                                    test_id=args.test_id, output=args.output, metadata=args.metadata,
                                    created_at=args.created_at))
        case "evals":
            _print_json(db.list_evals(args.name, version=args.version, model=args.model, test_id=args.test_id,
                                      since=args.since, until=args.until))
        case "compare":
            if args.min_score is not None and not 0 <= args.min_score <= 1:
                raise ValueError("--min-score must be between 0 and 1")
            comparison = db.compare(args.name, args.versions or None, model=args.model, test_id=args.test_id,
                                    since=args.since, until=args.until)
            if args.json:
                _print_json(comparison)
            else:
                _print_comparison(comparison)
            if args.min_score is not None:
                candidate = comparison.versions[-1]
                if candidate.average_score is None:
                    print(f"promptdb: v{candidate.prompt.version} has no matching evaluations", file=sys.stderr)
                    return 3
                if candidate.average_score < args.min_score:
                    print(f"promptdb: v{candidate.prompt.version} average {candidate.average_score:g} "
                          f"is below {args.min_score:g}", file=sys.stderr)
                    return 3
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "save":
            args.template = _read_template(args)
        if args.command == "import":
            args.payload = (sys.stdin.buffer.read().decode("utf-8") if args.source == "-"
                            else Path(args.source).read_text(encoding="utf-8"))
        with PromptDB(args.db, create=args.command in {"save", "import"}) as db:
            return _run(db, args)
    except (KeyError, OSError, ValueError, sqlite3.Error) as error:
        detail = error.args[0] if isinstance(error, KeyError) else str(error)
        print(f"promptdb: {detail}", file=sys.stderr)
        return 1
