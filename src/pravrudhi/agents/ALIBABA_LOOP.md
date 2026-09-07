# Alibaba loop: live verification blocked (2026-09-07)

`opencode:alibaba` invokes OpenCode's headless tool loop using the custom
`@ai-sdk/openai-compatible` provider and DashScope's Singapore endpoint.
The model is selectable through `build_agent(root, "opencode:alibaba", model)`.
The owner-owned, mode-0600 `~/.config/llm/dashscope.env` is parsed without
executing shell code, wrapped in the existing credentials module's `Secret`,
and passed only in the child environment. Configuration uses an environment
reference, and returned streams are redacted. No key is written by the adapter.
No batch, fine-tune, or deployment API is used.

## Actual dispatch transcript

OpenCode version: 1.18.29. Registry dispatch selected `qwen3-coder-plus`.
Workspace: this worktree's `src/pravrudhi/agents`.
Prompt:

> Use the read tool to read alibaba_agent.py in the current directory. Report
> the provider ID and default model. Do not write any files or run shell commands.

Captured output (ANSI styling removed):

```text
Availability: (True, 'ready (quota and network not probed)')
Live result: False 1

Error: Unexpected error
Unknown: FileSystem.open (/home/ss/.local/share/opencode/log/opencode.log)
```

No tool event or model answer was emitted. OpenCode failed at local startup,
before an observed provider request. This is not evidence that Qwen cannot use
tools, and it is not a successful live loop. The sandbox forbids writes to the
OpenCode state directory; approval is unavailable. Dependency downloads also
failed DNS resolution in this environment.

## Routing decision

`qwen-coder` now resolves to the loop adapter, but has no automatic tiers.
Keeping an unverified loop as a sentinel would conceal the same unattended
failure this change is meant to prevent. Sonnet handles mechanical and standard
work; the critical preference remains Astra. No success measurements were
invented or written to the routing history.

Before promotion, repeat the dispatch on a host with writable OpenCode runtime
state and network access, then run small edit-and-check tasks. Preserve the JSON
transcript, verify actual changes and checks independently, and record outcomes
at each tested tier. Only successful tiers should enter `tiers` and `declared`,
without `sentinel: true`; start with mechanical work. Harder tiers need their own
evidence. The adapter's successful completion flag is not a correctness score.

Offline tests replace the CLI transport and exercise model selection, tool-event
transcript retention, credential permissions/redaction, and failure handling.
They do not demonstrate provider compatibility or remaining free quota. This
adapter does not use the legacy shared free-tier client's quota ledger; remaining
quota must be checked operationally before enabling sustained load.

Provider/configuration references:
https://opencode.ai/docs/providers/#custom-provider
https://opencode.ai/docs/config/
