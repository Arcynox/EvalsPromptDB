"""Evaluate saved prompts with a local function, Ollama, OpenAI, Claude, or a compatible endpoint."""

import argparse
import json
import os
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from promptdb import PromptDB
from promptdb.scorers import json_match

PromptInput = str | list[dict[str, str]]


def _messages(value: PromptInput) -> list[dict[str, str]]:
    return [{"role": "user", "content": value}] if isinstance(value, str) else value


def _system_messages(value: PromptInput) -> list[dict[str, str]]:
    return [
        {**message, "role": "system"} if message["role"] == "developer" else message
        for message in _messages(value)
    ]


def _http_base(host: str) -> str:
    address = host if "://" in host else f"http://{host}"
    url = urlsplit(address)
    if url.scheme not in {"http", "https"} or not url.hostname or url.query or url.fragment or url.username is not None:
        raise ValueError("Provider host must be an HTTP(S) base URL without credentials")
    return address.rstrip("/")


def _post_json(endpoint: str, payload: dict[str, Any], timeout: float, api_key: str | None = None) -> dict[str, Any]:
    request = Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    if api_key:
        request.add_unredirected_header("Authorization", f"Bearer {api_key}")
    with urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise ValueError("Provider must return a JSON object")
    return result


def make_generator(
    provider: str, model: str, *, ollama_host: str = "http://127.0.0.1:11434", timeout: float = 90.0,
    ollama_context: int | None = None, max_tokens: int = 256,
    api_base: str = "http://127.0.0.1:1234/v1",
) -> Callable[[PromptInput], str]:
    if provider == "local":
        def generate_local(value: PromptInput) -> str:
            for message in reversed(_messages(value)):
                if message["role"] == "user":
                    return message["content"].upper()
            raise ValueError("The local demo needs a user message")

        return generate_local

    if provider == "ollama":
        endpoint = _http_base(ollama_host) + "/api/chat"
        options = {"temperature": 0, "num_predict": max_tokens}
        if ollama_context is not None:
            options["num_ctx"] = ollama_context

        def generate_ollama(value: PromptInput) -> str:
            result = _post_json(endpoint, {
                "model": model, "messages": _system_messages(value),
                "stream": False, "options": options,
            }, timeout)
            if result.get("done") is not True:
                raise ValueError("Ollama did not return a completed chat response")
            return result["message"]["content"]

        return generate_ollama

    if provider == "openai-compatible":
        endpoint = _http_base(api_base) + "/chat/completions"
        api_key = os.environ.get("PROMPTDB_ENDPOINT_API_KEY")

        def generate_compatible(value: PromptInput) -> str:
            result = _post_json(endpoint, {
                "model": model, "messages": _system_messages(value), "max_tokens": max_tokens, "stream": False,
            }, timeout, api_key)
            try:
                return result["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as error:
                raise ValueError("Expected a chat completion with message content") from error

        return generate_compatible

    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(timeout=timeout)

        def generate_openai(value: PromptInput) -> str:
            return client.responses.create(model=model, input=value, store=False, max_output_tokens=max_tokens).output_text

        return generate_openai

    if provider == "anthropic":
        from anthropic import Anthropic

        client = Anthropic(timeout=timeout)

        def generate_anthropic(value: PromptInput) -> str:
            instructions, turns = [], []
            for message in _system_messages(value):
                if message["role"] == "system":
                    if turns:
                        raise ValueError("Anthropic needs system and developer messages before conversation turns")
                    instructions.append(message["content"])
                else:
                    turns.append(message)
            parameters = {"system": "\n\n".join(instructions)} if instructions else {}
            result = client.messages.create(
                model=model, max_tokens=max_tokens, messages=turns, **parameters,
            )
            return "".join(block.text for block in result.content if block.type == "text")

        return generate_anthropic

    raise ValueError(f"Unknown provider: {provider}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=("local", "ollama", "openai", "openai-compatible", "anthropic"))
    parser.add_argument("name", help="saved prompt name")
    parser.add_argument("versions", type=int, nargs="+")
    parser.add_argument("--db", default="prompts.db")
    parser.add_argument("--model", help="provider model ID; optional for the local uppercase function")
    parser.add_argument("--run-id", help="shared ID for all versions in this invocation")
    parser.add_argument("--scorer", choices=("text", "json"), default="text")
    parser.add_argument("--ollama-host", default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    parser.add_argument("--api-base", default="http://127.0.0.1:1234/v1", help="base URL for openai-compatible")
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
    scorer = json_match if args.scorer == "json" else None
    with PromptDB(args.db, create=False) as db:
        prompts = [db.get(args.name, version) for version in versions]
        if not db.list_tests(args.name):
            parser.error("the prompt has no saved test cases")
        generate = make_generator(args.provider, model, ollama_host=args.ollama_host, timeout=args.timeout,
                                  ollama_context=args.ollama_context, max_tokens=args.max_tokens, api_base=args.api_base)
        for prompt in prompts:
            if prompt.messages:
                db.run_tests(args.name, version=prompt.version, model=model_key, generate_messages=generate,
                             scorer=scorer, run_id=run_id)
            else:
                db.run_tests(args.name, version=prompt.version, model=model_key, generate=generate,
                             scorer=scorer, run_id=run_id)
        summary = db.compare(args.name, versions, model=model_key, run_id=run_id)
    print(json.dumps({
        "run_id": run_id, "model": model_key,
        "versions": [{"version": row.prompt.version, "coverage": row.coverage,
                      "case_average_score": row.case_average_score} for row in summary.versions],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
