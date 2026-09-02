# Third-party notices

HARD1 does **not** redistribute the benchmark datasets listed below. `hard1 materialize` obtains them from their public upstream repositories at exact commits; hashes verify identity. Upstream terms continue to govern the fetched content.

## IFBench

- Repository: <https://github.com/allenai/IFBench>
- Frozen commit: `db69a6f05689830b0068b8f1529ebcfd2f3b164c`
- Code license: Apache-2.0
- Data license stated by upstream: ODC-BY-1.0, with Ai2 Responsible Use Guidelines and separate terms for third-party-model output data
- Citation: Valentina Pyatkin et al., “Generalizing Verifiable Instruction Following,” 2025.

HARD1 tracks source IDs, record hashes, instruction-category metadata, and evaluator compatibility code. It does not copy IFBench prompts or dataset rows.

## τ²/τ³ benchmark source

- Repository: <https://github.com/sierra-research/tau2-bench>
- Frozen commit: `fc0055dc4e0a316c3f83133267fbd6faaa770992`
- Repository license: MIT
- Core citations:
  - Victor Barres et al., “τ²-Bench: Evaluating Conversational Agents in a Dual-Control Environment,” arXiv:2506.07982, 2025.
  - Shunyu Yao et al., “τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains,” arXiv:2406.12045, 2024.
  - Quan Shi et al., “τ-Knowledge: Evaluating Conversational Agents over Unstructured Knowledge,” arXiv:2603.04370, 2026.

HARD1 tracks task IDs, hashes, domains, programmatic reward-basis categories, and aggregate structural counts. It does not copy tasks, policies, databases, conversations, or expected actions.

## ComplexMCP

- Repository: <https://github.com/ATH-MaaS/complex-mcp>
- Frozen commit: `617e963bd838bee5793a39e6b34165b79535828f`
- Repository license: MIT
- Citation: Yuanyang Li et al., “ComplexMCP: Evaluation of LLM Agents in Dynamic, Interdependent, and Large-Scale Tool Sandbox,” arXiv:2605.10787, 2026.

HARD1 tracks zero-based row IDs, query hashes, application/category metadata, and evaluator compatibility code. It does not copy Parquet rows, queries, expected environments, tool descriptions, or model trajectories.

## HARD1 code

Original HARD1 selection, verification, runner, scoring, and reporting code is licensed under Apache-2.0. Small compatibility patches are distributed solely to reproduce evaluation behavior against the named open-source revisions; their use remains subject to the corresponding upstream license.
