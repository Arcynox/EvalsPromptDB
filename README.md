<img src="assets/evals-promptdb.png" width="112" height="112" alt="Evals PromptDB icon">

# Evals PromptDB

[Leer en español](README.es.md)

Evals PromptDB is an embedded SQLite database for versioned LLM prompts and evaluation results. Keep templates, test cases, model outputs, and scores together in a local file. Use the Python library in your application or the CLI in a script or CI job. The core uses the Python standard library.

- Save immutable versions and select the current one by tag. Roll back by moving the tag to an earlier version.
- Run the same cases against different versions with a generator and scorer you supply.
- Compare template diffs, variable changes, and stored scores. Set a minimum score in CI.
- Export deterministic JSON for Git or transfer a consistent SQLite backup.

## Install

Use Python 3.10 or later. Install from this checkout.

```bash
python -m pip install .
promptdb --version
```

The distribution is named `evals-promptdb`. Import `PromptDB` from `promptdb`, and run `promptdb` or `python -m promptdb`. You can run `python -m promptdb` from the checkout without installing it.

If you installed PromptDB 0.1.0 or 0.2.0 from this project, remove the old distribution (`promptdb` or `promptdb-local`, respectively) from that environment before installing 1.0.0. Existing SQLite files open directly with 1.0.0.

## Quickstart in Python

Run this in a directory without a `prompts.db` file. The demo functions stand in for model calls so the example runs offline.

```python
from promptdb import PromptDB

with PromptDB("prompts.db") as db:
    v1 = db.save(
        "country",
        "Give the country containing {{city}}.",
        tags=["production"],
        metadata={"team": "examples"},
    )
    v2 = db.save(
        "country",
        "Return only the name of the country containing {{city}}.",
        tags=["experiment"],
    )
    case = db.add_test("country", input={"city": "Madrid"}, expected="Spain")

    current = db.get("country", tag="production")
    print(current.render(case.input))

    db.run_tests("country", version=v1.version, model="demo",
                 generate=lambda text: "Madrid is in Spain.")
    db.run_tests("country", version=v2.version, model="demo",
                 generate=lambda text: "Spain")

    report = db.compare("country", [v1.version, v2.version], model="demo")
    print(report.versions[1].diff)
    print(report.best.prompt.version)  # 2

    db.promote("country", v2.version, tag="production")
    db.promote("country", v1.version, tag="production")  # Roll back
```

The same case applies to both versions. By default, `run_tests()` scores text by exact match after trimming surrounding whitespace. A custom `scorer(output, expected)` can return any numeric score from 0 to 1 and is required for non-text expectations.

## Quickstart in the terminal

These commands use a fresh database named `cli.db`.

```bash
promptdb --db cli.db save country --template 'Give the country containing {{city}}.' --tag production
promptdb --db cli.db save country --template 'Return only the country name for {{city}}.' --tag experiment
promptdb --db cli.db get country --tag production --input '{"city":"Madrid"}'
promptdb --db cli.db add-test country --input '{"city":"Madrid"}' --expected '"Spain"'
promptdb --db cli.db eval country --version 2 --model demo --test-id 1 --score 0.87
promptdb --db cli.db compare country 1 2 --model demo --min-score 0.85
promptdb --db cli.db promote country 2 --tag production
```

Use `save --from-file template.txt` for a multiline UTF-8 template and `--from-file -` for standard input. Use `get --json` for a full record, `get --input` for a rendered template, and `compare --json` for a machine-readable comparison. For the full command list, run `promptdb --help`.

The last version included in a comparison is the candidate for `--min-score`. The command returns status `3` if its average is below the threshold or if no runs match the filters. A passing result returns `0`. Operational errors return `1`, and command-line syntax errors return `2`.

## Python API

| Operation | Behavior |
| --- | --- |
| `save(name, template, ...)` | Creates a new version. Accepts optional `version`, `variables`, `model`, `temperature`, `metadata`, and `tags`. |
| `get(name, version="latest", tag=None)` | Returns the highest matching version. Passing a number and a tag requires both to match. Raises `KeyError` when there is no match. |
| `Prompt.render(values)` | Replaces detected placeholders in one pass. Extra inputs are allowed. A missing input raises `KeyError`. |
| `list(tags=())` and `history(name)` | List versions. Filtering by tags requires every requested tag. History orders versions from oldest to newest. |
| `set_tags(name, version, tags)` | Replaces tags on one version. The CLI equivalent is `tag`. |
| `promote(name, version, tag="produccion")` | Moves a tag to exactly one version of that prompt in a single transaction. |
| `add_test(name, input=..., expected=...)` and `list_tests(name)` | Store and retrieve cases shared by all versions of a prompt. |
| `add_eval(name, version=..., score=..., model=..., ...)` | Records a score and optionally `test_id`, `output`, `metadata`, and an existing `created_at`. |
| `run_tests(name, model=..., generate=..., ...)` | Renders all cases, calls `generate(rendered_text)`, and stores the scored results in one transaction. Accepts `version`, `scorer`, and `metadata`. |
| `compare(name, versions=None, ...)` and `list_evals(name, ...)` | Read stored evaluations with filters for model, case, and date. |
| `delete(name, version)` | Removes one version, its tags, and its evaluations. Associated test cases remain attached to the prompt name. |

Placeholder names such as `{{city}}` and `{{ city }}` are detected in first-use order. If you pass `variables` explicitly, use the same set as in the template. Version numbers increase per name, including after deletion. Templates and model settings are immutable once saved. Use a new version for edits.

Pass an aware `datetime` for `created_at`, `since`, and `until`. The CLI accepts ISO 8601 timestamps with offsets, such as `2026-11-12T10:00:00-03:00`. Time windows include `since` and exclude `until`. The comparison uses the first requested version as the base of the diff. `report.best` returns the highest scored version and uses comparison order to break ties. Versions without matching runs have an average of `None`. Compare the same model and a consistent set of cases when using a score as a gate.

A batch starts from the prompt and cases visible at the beginning of the run. Model calls and scoring happen outside the SQLite transaction. If rendering, generation, scoring, or persistence fails, the batch leaves no partial results.

## JSON snapshots and SQLite backups

Use JSON when you want to inspect changes in Git, transfer the logical data, or load it into a new database.

```bash
promptdb --db prompts.db export > promptdb.json
promptdb --db restored.db import promptdb.json
promptdb --db restored.db history country
```

The snapshot includes templates, metadata, tags, inputs, expected values, outputs, dates, IDs, and version counters. The same database state produces the same UTF-8 JSON. Import requires a new or unused database and validates the complete snapshot in one transaction. Use `db.export_json()` to get a string and `db.import_json(payload)` to restore it from Python.

The JSON format is identified by `format: "evals-promptdb"` and `version: 1`. The version of the JSON format is independent of the package version. You can check a snapshot into Git and import it into a new SQLite file in CI.

Use SQLite backup when you want a standalone database file. The source can be open in other connections. The destination must be a new path in an existing directory on a filesystem that supports hard links.

```bash
promptdb --db prompts.db backup prompts-copy.db
promptdb --db prompts-copy.db history country
```

The equivalent API is `db.backup("prompts-copy.db")`. Open the copied file directly to use it. Use a separate `PromptDB` instance per thread. Set `PromptDB("prompts.db", timeout=30)` to configure the lock wait in seconds. Set `create=False` to require an existing database, or use `PromptDB(":memory:")` for a temporary database.

## OpenAI integration

Run the Python quickstart first. Install the optional SDK with `python -m pip install openai` and configure `OPENAI_API_KEY`. The generator below sends the rendered template to the Responses API. Evals PromptDB stores the returned text and score locally. [OpenAI Responses API reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create).

```python
from openai import OpenAI
from promptdb import PromptDB

client = OpenAI()
model = "gpt-4o-mini"

def generate(text):
    return client.responses.create(
        model=model,
        input=text,
        store=False,
    ).output_text

with PromptDB("prompts.db", create=False) as db:
    runs = db.run_tests("country", version=2, model=model, generate=generate)
    for run in runs:
        print(run.test_id, run.score, run.output)
```

## Development

Run the test suite with the standard library.

```bash
python -m unittest discover -s tests -v
```

GitHub Actions defines a test matrix for Python 3.10 to 3.14 on Linux and Python 3.14 on macOS and Windows. To build and check a source distribution and wheel locally, use the packaging tools below.

```bash
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
```

[Changelog](CHANGELOG.md) · [MIT license](LICENSE)
