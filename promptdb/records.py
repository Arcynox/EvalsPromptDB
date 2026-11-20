import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

PLACEHOLDER = re.compile(r"\{\{[ \t]*([^\W\d]\w*)[ \t]*\}\}")


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    template: str
    variables: tuple[str, ...]
    model: str | None
    temperature: float | None
    metadata: dict[str, Any]
    tags: tuple[str, ...]
    created_at: datetime
    messages: tuple[ChatMessage, ...] = ()

    @property
    def kind(self) -> Literal["text", "chat"]:
        return "chat" if self.messages else "text"

    def _check_variables(self, values: Mapping[str, Any]) -> None:
        missing = [name for name in self.variables if name not in values]
        if missing:
            raise KeyError(f"Missing prompt variables: {', '.join(missing)}")

    def render(self, values: Mapping[str, Any]) -> str:
        """Render a text prompt or a chat prompt's JSON representation."""
        if self.messages:
            return json.dumps(self.render_messages(values), ensure_ascii=False, indent=2)
        self._check_variables(values)
        return PLACEHOLDER.sub(lambda match: str(values[match[1]]), self.template)

    def render_messages(self, values: Mapping[str, Any]) -> list[dict[str, str]]:
        """Render a list of role/content messages suitable for a text chat API."""
        if not self.messages:
            return [{"role": "user", "content": self.render(values)}]
        self._check_variables(values)
        return [
            {"role": message.role, "content": PLACEHOLDER.sub(lambda match: str(values[match[1]]), message.content)}
            for message in self.messages
        ]


@dataclass(frozen=True)
class TestCase:
    id: int
    name: str
    input: dict[str, Any]
    expected: Any
    created_at: datetime
    archived_at: datetime | None = None


@dataclass(frozen=True)
class Evaluation:
    id: int
    name: str
    version: int
    score: float
    model: str
    test_id: int | None
    output: str | None
    metadata: dict[str, Any]
    created_at: datetime
    run_id: str | None = None


@dataclass(frozen=True)
class CaseScore:
    test_id: int
    evaluation_count: int
    average_score: float


@dataclass(frozen=True)
class PairedScore:
    case_count: int
    coverage: float
    baseline_score: float
    candidate_score: float
    delta: float


@dataclass(frozen=True)
class VersionComparison:
    prompt: Prompt
    diff: str
    added_variables: tuple[str, ...]
    removed_variables: tuple[str, ...]
    evaluation_count: int
    average_score: float | None
    case_scores: tuple[CaseScore, ...] = ()
    coverage: float | None = None
    paired: PairedScore | None = None
    case_average_score: float | None = None


@dataclass(frozen=True)
class Comparison:
    name: str
    base_version: int
    versions: tuple[VersionComparison, ...]
    tests: tuple[TestCase, ...]
    eligible_test_count: int = 0

    @property
    def best(self) -> VersionComparison | None:
        """Return the version with the highest row average, using comparison order for ties."""
        scored = (row for row in self.versions if row.average_score is not None)
        return max(scored, key=lambda row: row.average_score, default=None)
