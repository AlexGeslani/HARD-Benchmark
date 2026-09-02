# HARD1 methodology

## Design target

HARD1 freezes 60 cases—20 from each of IFBench, τ³, and ComplexMCP. It is intended to be harder than each full source population while retaining capable-model runway and a small number of known-pass harness canaries.

Selection priorities, in order, were:

1. preserve family topology;
2. cover all three materially different evaluation families;
3. retain difficulty and discrimination without selecting only the absolute hardest cases;
4. diversify model-independent structural categories;
5. control redundancy;
6. use pairwise construction-system balance only as a secondary diagnostic.

## Candidate universe

The tracked candidate table contains only:

- upstream family and source ID;
- source-record or query hash;
- structural features such as instruction IDs, domain, application set, difficulty level, and action/tool counts;
- eligibility/exclusion metadata;
- one of the four frozen construction strata.

It contains no prompts, conversations, model completions, gold state, expected answers, private paths, or endpoints.

T3 eligibility excludes every task whose reward basis contains `NL_ASSERTION`. The frozen T3 set therefore uses only executable/programmatic evaluation while retaining the canonical pinned user-simulator condition.

## Deterministic selection

`hard1.selection.select_manifest`:

1. filters to `eligible == true`;
2. applies the exact family/stratum quotas in `construction/policy.json`;
3. groups each quota pool by its model-independent structural category;
4. round-robins categories;
5. resolves ties with SHA-256 over the public seed, family, stratum, and source ID;
6. emits stable family-local case IDs.

The result is byte-reproducible under the frozen inputs. The committed acceptance procedure regenerates the manifest twice and compares both outputs with the tracked manifest.

## Frozen topology

| Family | `LUNA_FAIL_ONLY` | `QWEN_FAIL_ONLY` | `BOTH_FAIL` | `BOTH_PASS` |
|---|---:|---:|---:|---:|
| IF | 1 | 8 | 9 | 2 |
| T3 | 4 | 5 | 9 | 2 |
| MCP | 1 | 8 | 9 | 2 |

The two `BOTH_PASS` rows in every family are harness canaries. The IF and MCP slices preserve a clearly stronger first construction system; T3 remains comparatively close. These are construction properties, not publishable model rankings.

## Scoring

- **IF:** `1` only when all upstream strict instruction checks pass.
- **T3:** `1` only when upstream programmatic reward equals `1.0` exactly.
- **MCP:** `1` only when `recall == total` and `misbehave == 0`.
- **Family score:** arithmetic mean over 20 binary case scores.
- **Overall:** `(IF + T3 + MCP) / 3`.

A missing case, duplicate receipt, unknown case, family mismatch, malformed metric, or `INFRA_INVALID` receipt blocks scoring.

## Compatibility overlays

Two source-level overlays are frozen and hash-checked before application:

- IFBench mirrors strict-evaluator null-kwarg filtering into the loose diagnostic evaluator. Strict scoring is unchanged.
- ComplexMCP inserts one missing comma between `rid` and `oid` in the excluded-identifier set.

A separate FastMCP adapter normalizes Python-literal text to canonical JSON using `ast.literal_eval` and recursively rejects non-JSON values. It changes transport representation only.

All overlay source, patch, and resulting hashes are part of the harness identity. Unexpected drift fails closed.

## Limitations

- HARD1 is deliberately small and should be reported with all three family scores, not only Overall.
- Construction strata were derived from two archived screening systems. Their HARD1 scores are in-sample and unsuitable as independent comparison claims.
- The T3 simulator is stochastic infrastructure despite deterministic request settings; benchmark identity pins the model and parameters, not impossible bit-level API determinism.
- OpenAI-compatible endpoints differ in seed and tool-call support. Unsupported required semantics are infrastructure incompatibility, not model failure.
