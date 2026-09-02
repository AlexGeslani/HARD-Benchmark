# Reproducibility and endpoint contract

## Locked components

- HARD1 package: root `uv.lock`.
- IFBench and T3: exact upstream commits and their upstream `uv.lock` hashes.
- ComplexMCP: exact upstream commit plus `locks/complexmcp-requirements.lock.txt`, captured from the validated runtime.
- Dataset artifacts and upstream license files: SHA-256 locked in `locks/sources.lock.json`.
- Selection, runners, evaluators, and compatibility contracts: SHA-256 locked in `locks/harness.lock.json`.

## Source environments

After `hard1 materialize`:

```bash
uv sync --project .hard1/sources/IF --frozen
uv sync --project .hard1/sources/T3 --frozen
uv venv .hard1/sources/MCP/.venv --python 3.11
uv pip sync \
  --python .hard1/sources/MCP/.venv/bin/python \
  locks/complexmcp-requirements.lock.txt
```

Pass the relevant interpreter to `hard1 run --python ...` when it differs from the HARD1 CLI interpreter.

## Candidate endpoint

Required for all families:

- OpenAI-compatible base URL with `/v1/chat/completions`;
- stable model identifier supplied by the user;
- support for `temperature=0` and `seed=0`;
- sufficient context and output limits;
- credential supplied through `HARD1_MODEL_API_KEY` or another named environment variable.

The runners never serialize credential values. Endpoint URLs and raw local run artifacts belong under ignored `.hard1/` paths.

### IF

One user message per case; default output budget 8,192 tokens. The exact pinned IFBench strict evaluator determines the binary score. Loose results are retained only as diagnostics.

### T3

The candidate endpoint must support OpenAI-compatible structured tool calling through LiteLLM. The frozen user simulator is `openai/gpt-4.1-2025-04-14`, with temperature `0`, seed `0`, and 2,048 output tokens. It reads `OPENAI_API_KEY`. Candidate calls use temperature `0`, seed `0`, 8,192 output tokens, at most 100 steps, one trial, and no runner retries.

Selected tasks have no natural-language assertion basis. If one appears at runtime, execution stops as an integrity failure.

### MCP

ComplexMCP uses its textual `<tool>{...}</tool>` protocol. The endpoint must preserve tool directives in assistant message content. If an upstream Responses-style API emits tool directives in commentary items, an adapter must project commentary and final text in order; projecting only final-answer text is invalid. `hard1.evaluation.parse_complexmcp_tool_calls` is the focused compatibility fixture.

The runner starts the pinned loopback MCP services on ports 8000–8007 and 9000–9006, verifies health before and after each case, uses temperature `0`, seed `0`, an 8,192-token output cap, and at most 100 turns.

## Outputs

Real runs produce `run.json` and `receipts.jsonl` beneath a caller-selected output directory. Receipts intentionally include raw response/simulation artifacts for local audit. Keep them under `.hard1/`; they are excluded from version control and must not be submitted to this repository.

`hard1 score` requires exactly one valid terminal receipt for every manifest case. Combine family receipt streams before scoring the complete benchmark.

## Offline acceptance

The repository’s clean-clone acceptance uses local copies of already verified public sources and `mock-run`; it makes no model requests. A mock score demonstrates software plumbing only and must never be reported as model performance.
