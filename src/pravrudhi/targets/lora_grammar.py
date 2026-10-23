"""Grammar of LoRA candidates (L4 plan): two strategies, five execution families, bounded numeric ranges.

Anything a proposer emits outside this grammar is refused before it becomes a `propose` row.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

STRATEGIES = ("sft_rejection", "grpo_verifiable")
EXECUTION_FAMILIES = ("data_mixture", "optimiser", "adapter", "grpo", "template")


class LoraParams(BaseModel):
    """Bounded LoRA adapter settings: rank, alpha, dropout and which modules are adapted."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    r: int = Field(default=8, ge=1, le=64)
    alpha: int = Field(default=16, ge=1, le=256)
    dropout: float = Field(default=0.0, ge=0.0, le=0.3)
    target_modules: Literal["all-linear", "attention", "mlp"] = "all-linear"


class SftParams(BaseModel):
    """Bounded settings for the rejection-sampling fine-tune: how many kept samples, who samples, the filter, the start
    point, epochs and learning rate.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    n_kept: int = Field(default=512, ge=32, le=4096)  # how many kept (verified-correct) samples to train on
    teacher: Literal["incumbent", "Qwen/Qwen3-4B"] = (
        "incumbent"  # who samples: the trainee itself, or a stronger local model (distillation)
    )
    filter: Literal["all_correct", "shortest_correct", "longest_correct", "diverse_correct"] = "all_correct"
    init: Literal["base", "incumbent"] = "base"  # ADR-0016: start from the base model or continue the incumbent adapter
    epochs: int = Field(default=1, ge=1, le=3)
    lr: float = Field(default=1e-4, ge=1e-6, le=5e-3)
    warmup_ratio: float = Field(default=0.03, ge=0.0, le=0.2)
    max_seq_len: int = Field(default=1024, ge=256, le=2048)
    batch_size: int = Field(default=8, ge=1, le=32)


class GrpoParams(BaseModel):
    """Bounded settings for verifiable-reward GRPO training, sized to fit one card."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    # fp32 master weights on a 32 GiB card (bf16 LoRA-GRPO produced NaN gradients on this stack: ADR-0006 request);
    # the bounds keep peak memory under the card with the measured 28.4 GiB at group 4 x 128 tokens.
    steps: int = Field(default=20, ge=5, le=60)
    group_size: int = Field(default=4, ge=2, le=4)
    prompts_per_step: int = Field(default=1, ge=1, le=2)
    max_completion_tokens: int = Field(default=128, ge=64, le=192)
    lr: float = Field(default=5e-6, ge=1e-7, le=1e-4)
    beta_kl: float = Field(default=0.0, ge=0.0, le=0.1)


class LoraRecipe(BaseModel):
    """A model-track candidate as the proposer may write it: a strategy, an execution family, the adapter, SFT and GRPO
    settings, the evaluation template and a short rationale.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    strategy: Literal["sft_rejection", "grpo_verifiable"]
    execution_family: Literal["data_mixture", "optimiser", "adapter", "grpo", "template"]
    lora: LoraParams = LoraParams()
    sft: SftParams = SftParams()
    grpo: GrpoParams = GrpoParams()
    eval_template: Literal["gsm8k_v1", "gsm8k_v2_terse", "gsm8k_v3_boxed"] = "gsm8k_v1"
    rationale: str = Field(default="", max_length=400)

    @model_validator(mode="after")
    def _family_matches_strategy(self) -> LoraRecipe:
        if self.execution_family == "grpo" and self.strategy != "grpo_verifiable":
            raise ValueError("execution_family grpo requires strategy grpo_verifiable")
        if self.execution_family == "data_mixture" and self.strategy != "sft_rejection":
            raise ValueError("execution_family data_mixture requires strategy sft_rejection")
        return self

    def cost_est_gpu_h(self) -> float:
        """Rough cost prior before any measurement; the kernel's spend row replaces it."""
        if self.strategy == "sft_rejection":
            steps = self.sft.n_kept * self.sft.epochs / self.sft.batch_size
            return round(0.03 + steps * 1.2 / 3600 + 0.04, 4)  # load + ~1.2 s/step + paired eval
        return round(0.03 + self.grpo.steps * 20.0 / 3600 + 0.04, 4)  # ~20 s/step with HF rollouts


def parse_recipe(obj: dict[str, Any]) -> LoraRecipe | str:
    """Validate a proposer's JSON; return the recipe or the refusal reason (never raise into the loop)."""
    try:
        return LoraRecipe.model_validate(obj)
    except ValidationError as e:
        return "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
