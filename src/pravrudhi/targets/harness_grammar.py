"""Grammar of harness candidates for the agent track (Track H): a fixed model, a mutable scaffold.

Strategy level (ADR-0005): prompt_only | retry_policy | sampling_policy. Everything the agent runner reads is here."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

H_STRATEGIES = ("prompt_only", "retry_policy", "sampling_policy")
H_FAMILIES = ("system_prompt", "template", "feedback", "retries", "sampling", "context")

DEFAULT_SYSTEM = "You are an expert Python programmer. Write correct, efficient code and return only a single Python code block."
DEFAULT_TEMPLATE = "{question}\n\nImplement the function described above. Return only the code in one ```python block."
DEFAULT_FEEDBACK = (
    "Your previous solution failed these checks:\n{feedback}\n"
    "Fix the code and return the full corrected function in one ```python block."
)


class HarnessRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy: Literal["prompt_only", "retry_policy", "sampling_policy"]
    execution_family: Literal["system_prompt", "template", "feedback", "retries", "sampling", "context"]
    system_prompt: str = Field(default=DEFAULT_SYSTEM, min_length=10, max_length=1500)
    template: str = Field(default=DEFAULT_TEMPLATE, min_length=10, max_length=800)
    feedback_template: str = Field(default=DEFAULT_FEEDBACK, min_length=10, max_length=600)
    retries: int = Field(default=0, ge=0, le=3)
    n_samples: int = Field(default=1, ge=1, le=4)
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    max_new_tokens: int = Field(default=512, ge=256, le=1024)
    use_visible_tests: bool = True
    thinking: bool = False
    rationale: str = Field(default="", max_length=400)

    @model_validator(mode="after")
    def _placeholders(self) -> HarnessRecipe:
        if "{question}" not in self.template:
            raise ValueError("template must contain {question}")
        if "{feedback}" not in self.feedback_template:
            raise ValueError("feedback_template must contain {feedback}")
        for field, required in (("template", "{question}"), ("feedback_template", "{feedback}")):
            text = getattr(self, field)
            if required not in text:
                raise ValueError(f"{field} must contain {required}")
            others = sorted(set(re.findall(r"\{[a-zA-Z_]+\}", text)) - {required})
            if others:
                raise ValueError(f"{field} has unsupported placeholders {others}; only {required} is substituted")
        if self.strategy == "prompt_only" and (self.retries or self.n_samples > 1):
            raise ValueError("prompt_only recipes may not set retries or n_samples")
        return self

    def harness_json(self) -> dict[str, Any]:
        """The recipe as the ledger and `harness/agent/<bench>/harness.json` carry it: everything a reader needs
        to rebuild it. `strategy` and `execution_family` were excluded, so `parse_harness(harness_json())`
        failed on every promotion and each night silently started from the baseline (2026-09-11 review, H1)."""
        return self.model_dump(exclude={"rationale"})

    def cost_est_gpu_h(self) -> float:
        calls = self.n_samples * (1 + self.retries * 0.5)
        return round(0.02 + 0.03 * calls, 4)  # per 100-item rotation with a 1.7B model; the spend row replaces it


BASELINE = HarnessRecipe(strategy="prompt_only", execution_family="system_prompt")


def parse_harness(obj: dict[str, Any]) -> HarnessRecipe | str:
    try:
        return HarnessRecipe.model_validate(obj)
    except ValidationError as e:
        return "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())


H_GRAMMAR_DOC = """{
  "strategy": "prompt_only" | "retry_policy" | "sampling_policy",
  "execution_family": "system_prompt" | "template" | "feedback" | "retries" | "sampling" | "context",
  "system_prompt": "<10..1500 chars>",
  "template": "<10..800 chars, must contain {question}>",
  "feedback_template": "<10..600 chars, must contain {feedback}>",
  "retries": 0..3,            # re-ask with visible-test failures fed back (retry_policy / sampling_policy only)
  "n_samples": 1..4,          # best-of-n by visible tests (sampling_policy)
  "temperature": 0..1,
  "max_new_tokens": 256..1024,
  "use_visible_tests": true|false,
  "thinking": true|false,     # Qwen3 thinking mode (slower, longer)
  "rationale": "<= 400 chars"
}
Constraints: prompt_only may not set retries or n_samples > 1. Omitted fields take the baseline harness values."""


def harness_array_schema(k: int) -> dict[str, Any]:
    """JSON schema for exactly k HarnessRecipe objects, reduced to what a grammar compiler needs (types, enums,
    required keys, closed objects). Bounds and lengths stay with Pydantic validation after parsing."""
    props = {}
    for name, spec in HarnessRecipe.model_json_schema()["properties"].items():
        props[name] = {k2: v for k2, v in spec.items() if k2 in ("type", "enum")}
    item = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
    return {"type": "array", "items": item, "minItems": k, "maxItems": k}
