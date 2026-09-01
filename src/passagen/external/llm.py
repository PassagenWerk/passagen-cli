from dataclasses import dataclass, field
from enum import StrEnum

from passagen.llm import LlmProvider, LlmProviderError, LlmResponse


class LlmStage(StrEnum):
    FACT = "fact"
    SUMMARY = "summary"
    OUTLINE = "outline"


@dataclass(slots=True)
class TokenUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, response: LlmResponse | None) -> None:
        self.calls += 1
        if response is not None:
            self.input_tokens += response.input_tokens or 0
            self.output_tokens += response.output_tokens or 0


@dataclass(slots=True)
class LlmCallStats:
    by_stage: dict[LlmStage, TokenUsage] = field(
        default_factory=lambda: {stage: TokenUsage() for stage in LlmStage}
    )

    @property
    def total(self) -> TokenUsage:
        result = TokenUsage()
        for usage in self.by_stage.values():
            result.calls += usage.calls
            result.input_tokens += usage.input_tokens
            result.output_tokens += usage.output_tokens
        return result

    def record(self, stage: LlmStage, response: LlmResponse | None) -> None:
        self.by_stage[stage].add(response)


class TrackedLlmProvider:
    """Wrap an LLM provider so external-call accounting stays outside pipeline stages."""

    def __init__(self, provider: LlmProvider, stats: LlmCallStats | None = None) -> None:
        self.provider = provider
        self.stats = stats or LlmCallStats()

    @property
    def provider_name(self) -> str:
        return self.provider.provider_name

    @property
    def model(self) -> str:
        return self.provider.model

    def generate(self, stage: LlmStage, prompt: str, *, max_tokens: int) -> LlmResponse:
        try:
            response = self.provider.generate(prompt, max_tokens=max_tokens)
        except LlmProviderError:
            self.stats.record(stage, None)
            raise
        self.stats.record(stage, response)
        return response
