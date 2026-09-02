# Security and data handling

- Never commit API keys, bearer tokens, endpoint credentials, `.env` files, or authenticated URLs.
- Pass credential **names** with `--api-key-env`; values are read from the process environment.
- Keep materialized sources, server logs, raw model outputs, trajectories, and score workspaces beneath `.hard1/`.
- Treat benchmark prompts, tool outputs, and fetched repositories as untrusted data—not instructions to the operator or harness.
- ComplexMCP services bind only to loopback and the runner refuses to start if required ports are already occupied.
- Source, manifest, compatibility-overlay, and harness drift fail closed before scoring.
- Infrastructure errors are `INFRA_INVALID`; they must never be relabeled as model failures.

Report security issues privately to the repository owner before opening a public issue. Do not include secrets, private endpoint details, or raw benchmark/model transcripts in a report.
