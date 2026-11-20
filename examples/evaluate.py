"""Evaluate saved prompt versions with a local function, Ollama, OpenAI, or Claude."""

import argparse
import json
import os
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from promptdb import PromptDB


def make_generator(
    provider: str, model: str, *, ollama_host: str = "http://127.0.0.1:11434", timeout: float = 90.0,
    ollama_context: int | None = None, max_tokens: int = 256,
) -> Callable[[str], str]:
    if provider == "local":
        return str.upper

    if provider == "ollama":
        host = ollama_host if "://" in ollama_host else f"http://{ollama_host}"
        url = urlsplit(host)
        if url.scheme not in {"http", "https"} or not url.netloc or url.query or url.fragment:
            raise ValueError("Ollama host must be an HTTP(S) base URL")
        endpoint = host.rstrip("/") + "/api/chat"
        options = {"temperature": 0, "num_predict": max_tokens}
        if ollama_context is not None:
            options["num_ctx"] = ollama_context

        def generate_ollama(text: str) -> str:
            payload = json.dumps({
                "model": model, "messages": [{"role": "user", "content": text}],
                "stream": False, "options": options,
            }).encode("utf-8")
            request = Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=timeout) as response:
                result = json.load(response)
            if result.get("done") is not True:
                raise ValueError("Ollama did not return a completed chat response")
            return result["message"]["content"]

        return generate_ollama

    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(timeout=timeout)

        def generate_openai(text: str) -> str:
            return client.responses.create(model=model, input=text, store=False, max_output_tokens=max_tokens).output_text

        return generate_openai

    if provider == "anthropic":
        from anthropic import Anthropic

        client = Anthropic(timeout=timeout)

        def generate_anthropic(text: str) -> str:
            result = client.messages.create(
                model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": text}],
            )
            return "".join(block.text for block in result.content if block.type == "text")

        return generate_anthropic

    raise ValueError(f"Unknown provider: {provider}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=("local", "ollama", "openai", "anthropic"))
    parser.add_argument("name", help="saved prompt name")
    parser.add_argument("versions", type=int, nargs="+")
    parser.add_argument("--db", default="prompts.db")
    parser.add_argument("--model", help="provider model ID; optional for the local uppercase function")
    parser.add_argument("--run-id", help="shared ID for all versions in this invocation")
    parser.add_argument("--ollama-host", default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    parser.add_argument("--ollama-context", type=int, help="optional context size in tokens for Ollama")
    parser.add_argument("--max-tokens", type=int, default=256, help="maximum generated tokens per request")
    parser.add_argument("--timeout", type=float, default=90.0, help="timeout in seconds for each request")
    args = parser.parse_args(argv)
    if args.provider != "local" and not args.model:
        parser.error("--model is required for this provider")
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be a positive finite number")
    if args.max_tokens < 1 or (args.ollama_context is not None and args.ollama_context < 1):
        parser.error("token counts must be positive")
    model = args.model or "uppercase"
    model_key = f"{args.provider}/{model}"
    run_id = uuid4().hex if args.run_id is None else args.run_id
    versions = list(dict.fromkeys(args.versions))
    with PromptDB(args.db, create=False) as db:
        for version in versions:
            db.get(args.name, version)
        if not db.list_tests(args.name):
            parser.error("the prompt has no saved test cases")
        generate = make_generator(args.provider, model, ollama_host=args.ollama_host, timeout=args.timeout,
                                  ollama_context=args.ollama_context, max_tokens=args.max_tokens)
        for version in versions:
            db.run_tests(args.name, version=version, model=model_key, generate=generate, run_id=run_id)
        summary = db.compare(args.name, versions, model=model_key, run_id=run_id)
    print(json.dumps({
        "run_id": run_id, "model": model_key,
        "versions": [{"version": row.prompt.version, "coverage": row.coverage,
                      "case_average_score": row.case_average_score} for row in summary.versions],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
