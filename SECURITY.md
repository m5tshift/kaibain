# Security policy

Kaibain is a local, single-user application. The web server binds to
`127.0.0.1`, validates `Host`/`Origin` and rejects cross-site requests. It is
not designed to be exposed to a network or shared between users.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's
private vulnerability reporting instead:
**Security → Report a vulnerability** on this repository.

Include the affected version/commit, operating system, reproduction steps and
the impact you observed. You should receive an acknowledgement within a few
days. Fixes are released from the default branch; there are no maintained
release branches yet.

## Scope

In scope:

- the HTTP/web UI and its Host/Origin and CSRF protections;
- the MCP server and its ownership/version checks (`src/kaibain/store.py`);
- run supervision, cleanup fencing, checkpoint recovery and the Apply action
  (local branch publication through Git plumbing);
- backup export/restore.

Out of scope here (report to the respective project):

- microVM isolation, model orchestration and the executor adapter, which live
  in the separate [multi-agent-system](https://github.com/m5tshift/multi-agent-system)
  repository;
- Ollama, Microsandbox, Claude Code, Codex and other third-party software.

Agent registration, heartbeats and self-reported checks are **not** proof of
sandboxing or correctness; see [README.en.md](README.en.md) for the trust model.
