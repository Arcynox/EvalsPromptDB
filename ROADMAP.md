# Roadmap

Evals PromptDB focuses on making prompt changes reviewable and evaluations reproducible in a local SQLite file.

## Available in 1.2

- Versioned text and chat prompts, production tags, and rollback.
- Synchronous and asynchronous evaluation with case coverage and regression checks.
- Versioned JSON snapshots, SQLite backups, and read-only inspection.

## Areas for contributions

| Area | What would help |
| --- | --- |
| Evaluation workflows | Examples that use representative datasets and explain why each case matters. |
| Interoperability | Documented examples for additional model clients and common JSONL evaluation datasets. |
| Large datasets | Measurements of import and comparison performance, with reproducible fixtures. |
| Release reliability | Compatibility reports and regression tests for SQLite migrations and snapshot round trips. |

These are areas to explore. Open an [issue](https://github.com/Arcynox/EvalsPromptDB/issues) before starting a larger API or storage change so we can agree on its scope. See [CONTRIBUTING.md](CONTRIBUTING.md) for the PR process.
