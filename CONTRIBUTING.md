# Contributing

Thank you for your interest in this project. Please read this guide before opening a pull request.

## Code authorship policy

**Humans must not write or commit code in this repository.**

All code changes must be authored by AI coding agents (for example Cursor, Copilot, Codex, or comparable agentic tools). Human contributors may:

- Open issues and describe bugs, features, or requirements
- Review pull requests
- Approve, request changes, or merge PRs
- Update documentation that is not implementation code (where permitted by maintainers)

Humans must not hand-edit source files, scripts, configs, migrations, or tests to implement behavior. If you need a change, describe the goal clearly and let an AI agent produce the patch.

## Pull request workflow

Everything else follows the normal PR flow:

1. **Fork** the repository (or work on a branch if you are a maintainer).
2. **Describe the change** in an issue or in the PR description: problem, intended behavior, and acceptance criteria.
3. **Use an AI agent** to implement the change on your branch. The PR should reflect agent-authored commits or a clear agent-generated diff.
4. **Open a pull request** against `main` with:
   - A concise summary of what changed and why
   - Notes on how the change was tested
   - Links to related issues, if any
5. **Review** — maintainers will review for correctness, style, security, and fit with project conventions (`AGENTS.md`, `README.md`).
6. **Address feedback** — use an AI agent to apply review fixes; do not hand-patch code.
7. **Merge** — once approved and checks pass, a maintainer merges the PR.

## Expectations

- Keep PRs focused and reasonably sized.
- Do not commit secrets (`.env`, credentials, API keys). Use `.env.example` for documented placeholders only.
- Respect scraper rate limits and upstream sites (see `AGENTS.md`).
- Ensure `uv` / project tooling and existing conventions are followed.

## Questions

Open a GitHub issue for questions about process, scope, or whether a change is in scope. Implementation still goes through an AI agent via PR.
