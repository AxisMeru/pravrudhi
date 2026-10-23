# Overview

Pravrudhi (प्रवृद्धि) is a recursive self-improvement engine for language-model systems that you run on your own
hardware, over your own model and your own harness. The same engine also serves a screening product for Indian
criminal law (the Nyaya side): it reads facts a lawyer enters, checks them against the elements of an offence one
ingredient at a time, and refers back what it cannot support.

This page is a map of the repository. For the two-package design read {doc}`architecture`; for the commands see
{doc}`../reference/cli`.

## The two packages

| Package | Folder | Role |
|---|---|---|
| `pravrudhi_kernel` | `pravrudhi_kernel/src/pravrudhi_kernel/` | The evaluator: ledger schema, the hash-chained ledger writer and verifier, vendored statistics, the controller mathematics, sealed pools, benchmark scorers and the sandbox runner. No model client and no network access. |
| `pravrudhi` | `src/pravrudhi/` | The engine: it proposes, orchestrates and serves. It carries the command line, the HTTP server, the application logic, the coding-agent and multi-machine layers and the Nyaya product code. |

The dependency runs one way: the engine depends on the kernel, never the reverse.

## What is in the repository

| Folder | What it holds |
|---|---|
| `src/pravrudhi/` | The engine package. A per-package map is in {doc}`../reference/packages`. |
| `pravrudhi_kernel/` | The kernel package with its own tests. |
| `configs/` | Configuration files read by the engine ({doc}`../reference/configuration`). |
| `docs/` | Written documents: usage, architecture, the partner API, the evidence documents rendered from the ledger. |
| `sphinx/` | This documentation. |
| `app/` | The browser interface and desktop shell. |
| `deploy/`, `docker/` | Container images and deployment descriptions. |
| `harness/` | The mutable scaffold that the harness track improves, and its prompts. |
| `plugin/` | Skills for driving a project from a coding-agent session. |
| `proposals/` | Written proposals. |
| `supabase/` | The database schema and migrations used by the hosted product. |
| `verification/` | The Lean structure check used for the element-by-element screening. |
| `paper/` | The paper's sources. |
| `scripts/` | Command-line helpers, guards run in CI, and data-building scripts. |
| `tests/` | The engine's tests, with governance tests under `tests/governance`. |

## Where to start

- To run something: the quickstart in the repository `README.md`, then {doc}`../reference/cli`.
- To understand a screening: the partner API pages under `docs/api/` and {doc}`../reference/http-api`.
- To change code: {doc}`development`.
