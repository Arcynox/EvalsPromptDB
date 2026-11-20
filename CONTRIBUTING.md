# Contributing to Evals PromptDB

Bug reports, documentation fixes, and pull requests are welcome. If you find a bug, open an [issue](https://github.com/Arcynox/EvalsPromptDB/issues) with the PromptDB, Python, and operating system versions, the command or example that reproduces it, and the expected result. Use sample prompts and inputs you can share publicly.

## Open a pull request

1. Fork the repository and create a branch for your change.
2. Keep the PR focused. For a bug fix, include a regression test that fails before the fix.
3. Run the tests below and open a PR against `master`. In the description, explain what changed and how you verified it.
4. CI runs the compatibility matrix and checks the build. The maintainer reviews and merges the PR.

For changes to the public API, CLI, SQLite schema, or snapshot format, describe how existing callers and saved data behave after upgrading. Update both README files when user-facing behavior changes.

## Run the tests

From the repository root, with Python 3.10 or later:

```bash
python -m unittest discover -s tests -v
```

The suite uses the standard library and temporary databases. Provider tests use fixtures and run without API keys. See the [roadmap](ROADMAP.md) for areas where contributions would help.
