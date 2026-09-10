# Hard0.1 protocol

Hard0.1 was initially published as Hard1 v1.0 and was reclassified before the
next Hard1 generation. It retains the internal contract identifier `1.1.0`,
`hard1.v11` module path, receipt schema names, historical `HARD1-*` case IDs, and
append-only event identifiers so existing evidence remains verifiable. These
internal identifiers are provenance, not the current public name.

## Frozen measurement contract

- 60 selected cases: 20 IF, 20 MCP, and 20 T3.
- One valid terminal score per case for a standard 60-case model evaluation.
- Optional repeated trials are supplementary stability evidence and must be reported separately; they are not additional cases.
- Native source evaluators and programmatic acceptance criteria remain authoritative.
- Parsed output-limit, timeout, and answerless normal completions follow the preregistered configured-system policy.
- Infrastructure-invalid attempts do not consume schedule slots. They remain append-only custody and require explicit correction authority.
- Automatic retries, answer repair, scorer relaxation, fallback routes, and outcome-conditioned post-freeze case replacement are forbidden.

## Runtime and isolation

Every case uses a separate workspace. Candidate and simulator roles have separate request telemetry. T3 uses candidate-selfplay: the tested condition serves both roles without sharing conversation state. MCP starts the pinned source services on fresh loopback ports. IF uses the pinned strict evaluator.

Campaign identity binds the selected cases, source lock, runtime profile, candidate/simulator settings, retry policy, and runner source. Raw prompts, answers, credentials, and private provider configuration are excluded from public artifacts.

## Attempt and schedule custody

Attempt-terminal events are `VALID`, `ADJUDICATED`, and `INVALID`. Only `VALID` and `ADJUDICATED` complete a `(case_id, trial)` schedule slot. This distinction permits objective infrastructure failures and owner interruptions to close an attempt without silently consuming or replaying the scheduled trial.

## Public projection

Public reports use provider-neutral screening labels and sanitized aggregate evidence. Internal historical labels remain unchanged in private custody. Construction-system results are in-sample calibration evidence; they are not unbiased full-source accuracy estimates.
