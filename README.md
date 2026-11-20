<div align="center">
  <img src="assets/evals-promptdb.png" width="128" height="128" alt="Evals PromptDB icon">
  <h1>Evals PromptDB</h1>
  <p><strong>Local SQLite storage for versioned LLM prompts and evaluations.</strong></p>
  <p>
    <a href="https://pypi.org/project/evals-promptdb/"><img src="https://img.shields.io/pypi/v/evals-promptdb" alt="PyPI"></a>
    <a href="https://github.com/Arcynox/EvalsPromptDB/actions/workflows/ci.yml"><img src="https://github.com/Arcynox/EvalsPromptDB/actions/workflows/ci.yml/badge.svg" alt="Test and build status"></a>
    <a href="#install"><img src="https://img.shields.io/badge/Python-3.10%2B-6d4aff" alt="Python 3.10 and later"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-27896d" alt="MIT license"></a>
  </p>
  <p>
    <a href="README.es.md">Español</a> · <a href="#install">Install</a> · <a href="#quickstart-in-python">Python quickstart</a> · <a href="#quickstart-in-the-terminal">CLI quickstart</a> · <a href="#provider-examples">Providers</a>
  </p>
</div>

[Contributing](CONTRIBUTING.md) · [Roadmap](ROADMAP.md) · [Prompt improvement tutorial (Spanish)](docs/prompt-improvement.md)

Keep templates, test cases, model outputs, and scores in one `.db` file. Use the Python API in your application or the CLI in scripts and CI. The core uses the Python standard library.

## Prompt workflow

| Step | What you can do |
| --- | --- |
| **Version** | Save immutable templates and select the active version by tag. |
| **Evaluate** | Run the same cases across versions with a generator and scorer you supply. |
| **Compare and promote** | Inspect diffs, scores, and coverage. Gate promotion in CI and roll back by moving the tag. |
| **Share** | Track deterministic JSON in Git or transfer a consistent SQLite backup. |

## Install

Install from [PyPI](https://pypi.org/project/evals-promptdb/) using Python 3.10 or later.

```bash
python -m pip install evals-promptdb
promptdb --version
```

From a local checkout, use `python -m pip install .`.

The distribution is named `evals-promptdb`. Import `PromptDB` from `promptdb`, and run `promptdb` or `python -m promptdb`. You can run `python -m promptdb` from the checkout without installing it.

If you installed PromptDB 0.1.0 or 0.2.0 from this project, remove the old distribution (`promptdb` or `promptdb-local`, respectively) from that environment before installing `evals-promptdb`. Opening an older SQLite file with a writable connection migrates it in a single transaction. Use the upgraded package to open migrated files.

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
                 generate=lambda text: "Madrid is in Spain.", run_id="quickstart")
    db.run_tests("country", version=v2.version, model="demo",
                 generate=lambda text: "Spain", run_id="quickstart")

    report = db.compare("country", [v1.version, v2.version], model="demo", run_id="quickstart")
    print(report.versions[1].diff)
    print(report.versions[1].coverage)  # 1.0
    print(report.best.prompt.version)  # 2

    db.promote("country", v2.version, tag="production")
    db.promote("country", v1.version, tag="production")  # Roll back
```

The same case applies to both versions. By default, `run_tests()` scores text by exact match after trimming surrounding whitespace. A custom `scorer(output, expected)` can return any numeric score from 0 to 1 and is required for non-text expectations.

## Chat prompts

Store ordered text messages when the model needs distinct instruction and conversation roles. Variables work in every message. The supported roles are `system`, `developer`, `user`, and `assistant`.

```python
from promptdb import PromptDB

with PromptDB(":memory:") as db:
    prompt = db.save("translate", messages=[
        {"role": "system", "content": "Translate the user's text to {{language}}."},
        {"role": "user", "content": "{{text}}"},
    ])
    inputs = {"language": "Spanish", "text": "Good morning"}
    messages = prompt.render_messages(inputs)
    db.add_test("translate", input=inputs, expected="Buenos días")
    db.run_tests("translate", model="demo", generate_messages=lambda conversation: "Buenos días")
```

`Prompt.kind` distinguishes `text` and `chat`. `render_messages()` also accepts text prompts and wraps their rendered text in a single user message. `render()` returns JSON text for chat prompts. Use `generate_messages` to pass the rendered messages directly to your model client.

```bash
promptdb --db chat.db save translate --messages - <<'JSON'
[{"role":"system","content":"Translate to {{language}}."},{"role":"user","content":"{{text}}"}]
JSON
promptdb --db chat.db get translate --input '{"language":"Spanish","text":"Good morning"}'
```

## Quickstart in the terminal

These commands use a fresh database named `cli.db`.

```bash
promptdb --db cli.db save country --template 'Give the country containing {{city}}.' --tag production
promptdb --db cli.db save country --template 'Return only the country name for {{city}}.' --tag experiment
promptdb --db cli.db get country --tag production --input '{"city":"Madrid"}'
promptdb --db cli.db add-test country --input '{"city":"Madrid"}' --expected '"Spain"'
promptdb --db cli.db eval country --version 1 --model demo --run-id demo-1 --test-id 1 --score 0.83
promptdb --db cli.db eval country --version 2 --model demo --run-id demo-1 --test-id 1 --score 0.87
promptdb --db cli.db compare country 1 2 --model demo --run-id demo-1 --min-score 0.85 --min-coverage 1 --max-regression 0.02
promptdb --db cli.db promote country 2 --tag production
```

<details>
<summary>Example comparison output</summary>

```text
Version  Variables  Added  Removed  Evals  Avg score  Cases  Case avg  Paired delta
1        city       -      -        1      0.830      1/1    0.830     -
2        city       -      -        1      0.870      1/1    0.870     +0.040 (1)
```

</details>

Use `save --from-file template.txt` for a multiline UTF-8 template and `--from-file -` for standard input. Use `get --json` for a full record, `get --input` for a rendered template, and `compare --json` for a machine-readable comparison. For the full command list, run `promptdb --help`.

### Quality gates in CI

The last version in the comparison is the candidate. The first is the baseline. Pass the same `--model` and `--run-id` used when recording the scores so the gate reads one model and one run.

| Flag | Condition for passing |
| --- | --- |
| `--min-score 0.85` | The candidate meets the score threshold. With a coverage or regression gate, each scored case has equal weight and only scores linked to a case count. On its own, `--min-score` uses the average of all matching evaluation rows. |
| `--min-coverage 1` | The candidate has scores for 100% of eligible cases. Without `--test-id`, all saved cases for the prompt are eligible. Repeated scores on one case count once toward coverage. |
| `--max-regression 0.02` | The candidate's case average drops by at most 0.02 against the baseline on their shared cases. Shared coverage must reach `--min-coverage`, or 100% if that flag is omitted. |

A failed gate returns status `3`, including when required scores or cases are missing. A passing result returns `0`. Operational errors return `1`, and command-line syntax errors return `2`. The JSON report includes `case_scores`, `coverage`, `case_average_score`, and `paired` for each version, alongside the original row average.

## Python API

| Operation | Behavior |
| --- | --- |
| `save(name, template, ...)` | Creates a version of a text prompt or accepts `messages=` for chat. Also accepts `version`, `variables`, `model`, `temperature`, `metadata`, and `tags`. |
| `get(name, version="latest", tag=None)` | Returns the highest matching version. Passing a number and a tag requires both to match. Raises `KeyError` when there is no match. |
| `Prompt.render(values)` | Replaces detected placeholders in one pass. Extra inputs are allowed. A missing input raises `KeyError`. |
| `list(tags=())` and `history(name)` | List versions. Filtering by tags requires every requested tag. History orders versions from oldest to newest. |
| `set_tags(name, version, tags)` | Replaces tags on one version. The CLI equivalent is `tag`. |
| `promote(name, version, tag="produccion")` | Moves a tag to exactly one version of that prompt in a single transaction. |
| `add_test(name, input=..., expected=...)` and `list_tests(name)` | Store cases shared by all versions of a prompt and list the active cases. |
| `get_test(name, test_id)` | Read a case by ID, including an archived case. |
| `set_test_archived(name, test_id, archived=True)` and `replace_test(...)` | Manage the active suite while keeping the previous case and its evaluations available. |
| `add_eval(name, version=..., score=..., model=..., ...)` | Records a score and optionally `test_id`, `run_id`, `output`, `metadata`, and an existing `created_at`. |
| `run_tests(name, model=..., generate=..., ...)` | Renders active cases, calls the generator, and stores the scored results in one transaction. Select a version or tag and optionally pass `test_ids` to run a subset. |
| `await arun_tests(name, model=..., generate=..., ...)` | Awaits a text or message generator and supports a synchronous or asynchronous scorer. |
| `compare(name, versions=None, ...)` and `list_evals(name, ...)` | Read stored evaluations with filters for run ID, model, case, and date. |
| `delete(name, version)` | Removes one version, its tags, and its evaluations. Associated test cases remain attached to the prompt name. |

Placeholder names such as `{{city}}` and `{{ city }}` are detected in first-use order. If you pass `variables` explicitly, use the same set as in the template. Version numbers increase per name, including after deletion. Templates and model settings are immutable once saved. Use a new version for edits.

Pass an aware `datetime` for `created_at`, `since`, and `until`. The CLI accepts ISO 8601 timestamps with offsets, such as `2026-11-12T10:00:00-03:00`. Time windows include `since` and exclude `until`. The comparison uses the first requested version as the base of the diff. `report.best` uses the highest `average_score` across matching rows and breaks ties in comparison order. Versions without matching runs have an average of `None`. Compare the same model and a consistent set of cases when using a score as a gate.

A batch starts from the prompt and cases visible at the beginning of the run. Model calls and scoring happen outside the SQLite transaction. If rendering, generation, scoring, or persistence fails, the batch leaves no partial results. `run_tests()` assigns an ID to the batch by default. Pass the same `run_id` to the baseline and candidate when evaluating them together.

Use `arun_tests()` with an async client in an event loop. It keeps SQLite transactions out of provider `await` calls. Both runners accept `tag` to select a deployed version and `test_ids` to run selected active cases in the requested order. A comparison measures coverage against the cases eligible for that comparison.

### Maintaining test cases

Use `archive-test` to retire a case or `restore-test` to make it active again. Use `replace-test NAME ID --input ... --expected ...` to archive the old case and create a new case with its own ID in one transaction. `get-test` and `evals` still show the original case and scores. By default, `run_tests`, `tests`, and `compare` use active cases. Pass `include_archived=True` to `list_tests()` or `compare()`, or `--include-archived` to `tests` or `compare`, to include archived cases in an audit.

### Scoring structured outputs

Use `from promptdb.scorers import json_match` and pass `scorer=json_match` to `run_tests()` for a JSON expectation. Field order and insignificant whitespace are ignored. Array order and the difference between numbers and booleans matter. Malformed JSON receives a zero score and its output stays in the evaluation record.

To score output produced by another tool, pass `eval --test-id ID --scorer json --output-file response.json` with the prompt name, version, and model. Use `--output-file -` for stdin. The provider example also accepts `--scorer json`.

## JSON snapshots and SQLite backups

Use JSON when you want to inspect changes in Git, transfer the logical data, or load it into a new database.

```bash
promptdb --db prompts.db export > promptdb.json
promptdb --db restored.db import promptdb.json
promptdb --db restored.db history country
```

The snapshot includes templates, metadata, tags, inputs, expected values, outputs, dates, IDs, and version counters. The same database state produces the same UTF-8 JSON. Import requires a new or unused database and validates the complete snapshot in one transaction. Use `db.export_json()` to get a string and `db.import_json(payload)` to restore it from Python.

The JSON format is identified by `format: "evals-promptdb"` and `version: 4`. It stores chat messages as JSON arrays and preserves which cases are archived. Imports also accept formats 1–3. You can check a snapshot into Git and import it into a new SQLite file in CI.

Use SQLite backup when you want a standalone database file. The source can be open in other connections. The destination must be a new path in an existing directory on a filesystem that supports hard links.

```bash
promptdb --db prompts.db backup prompts-copy.db
promptdb --db prompts-copy.db history country
```

The equivalent API is `db.backup("prompts-copy.db")`. Open the copied file directly to use it. Use a separate `PromptDB` instance per thread. Set `PromptDB("prompts.db", timeout=30)` to configure the lock wait in seconds. Set `create=False` to require an existing database, or use `PromptDB(":memory:")` for a temporary database.

Use `PromptDB("prompts.db", read_only=True)` or `promptdb --read-only` for an existing, current-schema database that should stay untouched. Reads, comparisons, exports, and backups can use that connection. `db.check()` returns SQLite integrity and foreign-key findings.

```bash
promptdb --db empty.db init
promptdb --db prompts.db --read-only check
promptdb --db prompts.db --read-only export --output snapshot.json
```

`export --output` creates a new UTF-8 file without replacing an existing path. The CLI also uses UTF-8 for prompt and JSON pipes, and `import` accepts snapshots with a UTF-8 BOM.

## Provider examples

Run the Python quickstart first. From the checkout, `python -m examples.evaluate` runs saved cases over any number of versions with one shared run ID. It supports Ollama, OpenAI, Anthropic, OpenAI-compatible Chat Completions servers, and a deterministic uppercase function for offline tests.

```bash
python -m examples.evaluate ollama country 1 2 --model qwen2.5-coder:7b --run-id ollama-smoke
promptdb compare country 1 2 --model ollama/qwen2.5-coder:7b --run-id ollama-smoke --min-coverage 1
```

Ollama uses its local HTTP API and an installed model. Set `--ollama-host` to choose a server. OpenAI uses the optional `openai` SDK and `OPENAI_API_KEY`. Anthropic uses the optional `anthropic` SDK and `ANTHROPIC_API_KEY`. Pass `--model` with the ID for that provider. The script prints the model key and run ID to use with `promptdb compare`. [Ollama Chat API](https://docs.ollama.com/api/chat) · [Anthropic Messages API](https://platform.claude.com/docs/en/api/messages)

Use `--timeout` to allow for a cold start, `--max-tokens` to limit generated tokens, or `--ollama-context` to set the local context size.

Use `openai-compatible --api-base` with a base URL such as `http://127.0.0.1:8000/v1` for a server that exposes Chat Completions. Set `PROMPTDB_ENDPOINT_API_KEY` if that server requires a bearer token. The example sends developer messages as system instructions to Ollama and compatible servers. Anthropic takes system and developer instructions before the first conversation turn. [vLLM compatible server documentation](https://docs.vllm.ai/en/latest/serving/openai_compatible_server/)

### OpenAI from Python

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

The suite covers migrations, snapshots, prompt rendering, and evaluation gates. It runs offline with temporary databases. See [Contributing](CONTRIBUTING.md) for the PR workflow.

GitHub Actions tests CPython 3.10–3.15 and PyPy 3.11 on Linux, CPython 3.10 and 3.15 on Windows, and CPython 3.15 on macOS. The wheel includes type annotations for consumers and has no required third-party runtime dependencies. Use the packaging tools below to build and check the distributions locally.

```bash
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
```

[Changelog](CHANGELOG.md) · [MIT license](LICENSE)
