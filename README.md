# HARD1

**HARD1** is the first release of the **Hard-tail Agentic Reliability Dataset**: a frozen 60-case evaluation slice for instruction following, conversational tool use, and stateful multi-tool execution.

HARD1 is intentionally small and auditable. It publishes source IDs, hashes, deterministic selection inputs, evaluator contracts, and materialization logic—not copied prompts, gold answers, source datasets, or model transcripts.

## Composition

| Family | Cases | What it measures | Binary pass condition |
|---|---:|---|---|
| HARD1-IF | 20 | Verifiable instruction following | Every strict IFBench instruction check passes |
| HARD1-T3 | 20 | Multi-turn agent/user tool interaction | Upstream programmatic reward is exactly `1.0` |
| HARD1-MCP | 20 | Dynamic, interdependent MCP tool use | Full expected state recall and zero misbehavior |

`HARD1 Overall` is the equal-weight arithmetic mean of the three family scores. Every case scores `0` or `1`; infrastructure failures are invalid and never converted to model failures.

## Quick verification without inference

Prerequisites: Git, Python 3.11+, and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --frozen
uv run hard1 select \
  --candidates construction/candidates.json \
  --policy construction/policy.json \
  --out /tmp/hard1-regenerated.json
cmp manifests/hard1-v1.json /tmp/hard1-regenerated.json

uv run hard1 verify \
  --manifest manifests/hard1-v1.json \
  --policy construction/policy.json \
  --candidates construction/candidates.json

uv run hard1 mock-run \
  --manifest manifests/hard1-v1.json \
  --out .hard1/mock/receipts.jsonl
uv run hard1 score \
  --manifest manifests/hard1-v1.json \
  --receipts .hard1/mock/receipts.jsonl \
  --out .hard1/mock/score.json
uv run hard1 report \
  --score .hard1/mock/score.json \
  --out .hard1/mock/report.md
```

The mock run exercises receipt validation, binary scoring, equal-family aggregation, and reporting. It is explicitly not a model result.

## Materialize public sources

```bash
uv run hard1 materialize \
  --lock locks/sources.lock.json \
  --cache .hard1/sources

uv run hard1 verify \
  --manifest manifests/hard1-v1.json \
  --policy construction/policy.json \
  --candidates construction/candidates.json \
  --source-lock locks/sources.lock.json \
  --source-cache .hard1/sources \
  --mcp-index locks/mcp-query-index.json \
  --harness-lock locks/harness.lock.json \
  --repo-root .
```

Materialization checks out exact public commits and verifies dataset, environment-lock, and license hashes. Local source overrides are supported as `--local-source IF=/path`, `T3=/path`, and `MCP=/path`.

## Run against a model endpoint

The candidate model endpoint must expose OpenAI-compatible `/v1/chat/completions`. Credentials are read only from an environment-variable name and are never placed in command arguments or tracked files.

```bash
export HARD1_MODEL_API_KEY='...'
uv run hard1 run \
  --family IF \
  --source-cache .hard1/sources \
  --manifest manifests/hard1-v1.json \
  --endpoint https://your-endpoint.example \
  --model your-model-id \
  --out .hard1/runs/your-model/if
```

Use `--dry-run` first to inspect the command without dispatching inference. Family-specific environment preparation and transport requirements are documented in [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md).

HARD1-T3 has one deliberate exception to provider neutrality: its user simulator is part of the frozen benchmark condition and is pinned to OpenAI `gpt-4.1-2025-04-14`. Candidate model serving remains provider-neutral. Selected T3 tasks are programmatic-only; natural-language assertion judging is forbidden.

## Construction labels

The exact stratum names `LUNA_FAIL_ONLY`, `QWEN_FAIL_ONLY`, `BOTH_FAIL`, and `BOTH_PASS` are stable construction labels. They refer to two archived screening systems used only to define the hard tail. This repository does not map those labels to private model aliases, providers, endpoints, or transcripts. Construction-system performance on HARD1 is in-sample selection evidence, not an independent benchmark result.

## Scope and non-goals

HARD1 is a local CLI and file format. It does not include a hosted service, leaderboard, database, scheduler, or platform component. It does not redistribute the upstream benchmark data. See [METHODOLOGY.md](METHODOLOGY.md), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), and [SECURITY.md](SECURITY.md).
