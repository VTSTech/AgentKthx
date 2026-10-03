# Security Policy

> AgentKthx is a stdlib-only agentic framework. The zero-runtime-dependency design (`dependencies = []`) is itself a security feature: nothing enters the install graph that we don't ship. This document describes how to report vulnerabilities, what defenses the codebase already provides, and what limitations are currently known.

---

## Supported Versions

| Version | Supported | Notes |
|---------|-----------|-------|
| `0.7.x` (current `main`) | ✅ Active | All security fixes land here first |
| Previous minor (`0.6.x`) | ⚠️ Best-effort | Critical fixes backported on request |
| Older (`<= 0.5.x`) | ❌ Not supported | Upgrade required |

PyPI publishes the latest supported release. The `main` branch may carry unreleased fixes; if you're reporting a vulnerability, check `main` first.

---

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security reports.**

Use one of these private channels:

1. **GitHub Security Advisories** (preferred) —
   [github.com/VTSTech/AgentKthx/security/advisories/new](https://github.com/VTSTech/AgentKthx/security/advisories/new)
   This enables private collaboration and a CVE assignment workflow.
2. **Email** — `veritas@vts-tech.org` with subject line `[SEC] AgentKthx: <short summary>`.
   PGP preferred if you have it; plaintext accepted.

Please include, where applicable:

- Affected version (or commit SHA)
- Minimal reproducer (a script, a CLI invocation, a crafted tool input)
- Whether the issue requires a privileged position (e.g., must already be a plugin author, must control a soul file, must be on the same host as a `turbo start` server)
- Whether you've confirmed it against `main`
- Suggested fix or affected file paths, if known

### Response timeline

| Event | Target |
|-------|--------|
| Acknowledgement of receipt | 72 hours |
| Initial assessment (valid / invalid / needs more info) | 7 days |
| Fix or mitigation for confirmed high/critical issues | 30 days |
| Coordinated disclosure ( advisory published, CVE requested) | After release containing the fix, or 90 days from report — whichever is sooner |

We will credit reporters in the advisory unless you ask to remain anonymous.

---

## Scope

### In scope

- The `agentkthx/` Python package (core, CLI, backends, plugins, tools, soul, skills)
- The plugin manifest format and loader (`agentkthx/plugins/_loader.py`)
- The bundled souls (`agentkthx/souls/`) and skills (`agentkthx/skills/`)
- The `audit/`, `scripts/`, and `patches/` directories that ship with releases
- The build and release workflow (`.github/workflows/`)

### Out of scope

- **Third-party runtime dependencies** — there are none. If you find a vulnerability in the Python standard library itself, report it to [psf](https://github.com/python/cpython/security/policy) — but tell us too, so we can document a workaround.
- **User-supplied souls and skills** that ship outside the repo. We'll still help with safe-load patterns, but the artifact itself is the user's responsibility.
- **Local backend daemons** (Ollama, llama-server / TurboQuant, BitNet). If `agentkthx` correctly forwards a request and the underlying daemon misbehaves, the bug lives upstream.
- **The `update_check.py` behavior of fetching from `pypi.org` and `api.github.com` on every CLI invocation.** This is intentional and `WONTFIX` (ROB-05). Set `AGENTKTHX_NO_UPDATE_CHECK=1` to disable it. We will not treat the existence of the check itself as a vulnerability; we will treat any **leak of credentials or PII** through the check as critical.
- **Denial-of-service via prompt injection** at the model layer. We mitigate prompt injection in tool inputs via `sanitize_tool_output` (ANSI strip + secret redaction + 8 KB truncation), but we cannot prevent a sufficiently capable model from being tricked. This is a category-wide limitation, not an AgentKthx-specific bug.
- **Attacks that require the attacker to already be the operator** (e.g., "I can run arbitrary Python by writing a malicious skill" — yes, skills are code; do not load skills you do not trust).

---

## Built-in defenses

The codebase implements defense-in-depth across several surfaces. Contributors adding new code should understand these primitives and reuse them rather than reinventing.

### Path safety — `core/helpers.py:validate_path`

- Allowed-prefix check against an explicit list of writable roots
- **Known limitation (ROB-09, open):** uses `os.path.abspath` rather than `os.path.realpath`, so symlink traversal within an allowed prefix is not blocked. Until ROB-09 lands, do not rely on `validate_path` to neutralize symlink-based escapes; treat it as a path-shape guard, not a sandbox boundary.

### Command safety — `core/helpers.py:sanitize_command`

- Denylist + shell/heredoc block; clamped shell timeout (`max(1, min(timeout, 300))`)
- **Limitation:** denylist-based. Sophisticated shell escapes are an arms race; do not treat `shell()` as a sandbox. If you need a sandboxed subprocess, use `tools/sandboxed_repl.py` (AST-walking `safe_eval`) instead.

### URL safety — `core/helpers.py:is_safe_url`

- `ipaddress`-based SSRF guard with bounded DNS (5 s / 32 records, fail-closed)
- 5-hop redirect budget via `_SSRFSafeRedirectHandler` in `tools/builtins.py`
- RFC1918 awareness — note ROB-28/30: `_is_local_base_url` excludes `172.16/12` so backends in that range take the remote-probe path (documented in-code, not a security bug, but a source of confusion)

### Tool-output sanitization — `core/helpers.py:sanitize_tool_output`

- Wraps every tool result in `<tool_output>` tags with:
  - 8 KB truncation
  - Secret redaction (regex-based — best-effort, not a guarantee)
  - ANSI escape stripping
- Runs on **every** tool result before it reaches memory or the model. New tool authors do not need to call this manually; the dispatch layer does it for you.

### Safe evaluation — `core/safe_eval.py`

- AST-walking replacement for `eval()` used by the calculator tool
- Rejects calls, attribute access, and other side-effecting nodes

### Plugin supply chain — `plugins/_loader.py`

- Plugin manifests may declare a `sha256` pin
- When a pin is present, the loader **fails closed** — the plugin is not loaded if the hash does not match
- **Known limitation (SEC-13, open):** pins are **opt-in**. A manifest without a pin loads silently. There is no `AGENTKTHX_REQUIRE_PLUGIN_PINS=1` enforcement mode yet. Until SEC-13 lands, treat any plugin directory on your path as trusted-by-default and audit manifests manually.

### Local backend default permissions

- `PersistentMemory` SQLite DBs created with `0o600` perms, parent dir `0o700`
- Per-DB-path write locks via realpath-keyed `WeakValueDictionary`

### Memory model

- Sliding-window message count + opt-in token-tier pruning (`MemoryConfig.max_tokens`, default `0` = disabled)
- Token tier is opt-in precisely so a misconfigured `max_tokens` cannot silently truncate context the user expected to see

---

## Known security-relevant findings

These are tracked in `audit/audit.md` and reproduced here so security-conscious operators can make informed decisions without reading the full audit.

| ID | Severity | Summary | Mitigation |
|----|----------|---------|------------|
| **SEC-09** | High | ACP plugin defaults to Basic-Auth-over-HTTP | Do not enable ACP on an untrusted network; configure HTTPS or set `AGENTKTHX_NO_UPDATE_CHECK=1` is unrelated — for ACP, run behind a TLS-terminating reverse proxy until SEC-09 lands |
| **SEC-13** | Medium | Plugin sha256 pins are opt-in with no enforcement mode | Audit `~/.agentkthx/plugins/` manifests manually; only install plugins from sources you control |
| **ROB-09** | Medium | `validate_path` uses `abspath` not `realpath` — symlink traversal within an allowed prefix is not blocked | Do not place writable tool directories on filesystems where untrusted users can create symlinks inside them |
| **ROB-33** | Medium (Windows-only) | `_is_process_alive` in `plugins/turboquant/turbo.py` calls `os.kill(pid, 0)`, which on Windows is `TerminateProcess` — a stale `turbo.state` file with a reused PID can kill an unrelated process | Run `turbo start` + `chat` on Windows only against a freshly-started server; delete `~/.agentkthx/turbo.state` before startup if PID reuse is suspected. POSIX is safe (`/proc` zombie check). |
| **ROB-05** | WONTFIX | `update_check.py` makes 3 sequential HTTPS requests on every CLI invocation | `export AGENTKTHX_NO_UPDATE_CHECK=1` |

If you believe you've found a new issue that should appear on this list, file a private report — see **Reporting a Vulnerability** above.

---

## Update check transparency

`agentkthx/update_check.py` runs on every CLI invocation and makes three HTTPS requests:

1. `https://pypi.org/pypi/agentkthx/json` — current published version
2. `https://api.github.com/repos/VTSTech/AgentKthx/commits` — recent commit history
3. `https://raw.githubusercontent.com/VTSTech/AgentKthx/main/agentkthx/__init__.py` — version string

**No credentials, no PII, no telemetry beyond the HTTP request itself.** The check is owner-blessed (ROB-05, WONTFIX) because the project's own refresh script depends on it.

To disable:

```sh
export AGENTKTHX_NO_UPDATE_CHECK=1
```

Set this in your shell rc file for permanent effect. The check is also skipped in test environments automatically.

---

## Disclosure policy

- We practice **coordinated disclosure**. Reports stay private until a fix is available (or the 90-day deadline lapses).
- We will request a CVE for confirmed high/critical issues via GitHub Security Advisories.
- Credit is given by default; anonymity honored on request.
- We will not take legal action against good-faith reporters.

---

## Hardening recommendations for operators

If you're deploying AgentKthx in a shared or production environment:

1. **Set `AGENTKTHX_NO_UPDATE_CHECK=1`** if outbound network calls during CLI startup are unacceptable.
2. **Pin your plugins.** Add `sha256` to every manifest in `~/.agentkthx/plugins/`. Until SEC-13 lands, this is your only supply-chain control.
3. **Run local backends on POSIX when possible.** ROB-33 makes Windows a riskier host for `turbo start`.
4. **Restrict writable tool paths.** `validate_path` only enforces an allowlist; ensure no untrusted user can create symlinks inside those roots (ROB-09).
5. **Don't enable ACP on plain HTTP.** SEC-09 has not landed; use a TLS-terminating reverse proxy.
6. **Audit souls and skills before loading.** Souls are persona packages (YAML/markdown) and are mostly safe; **skills are Python code** and execute with full process privileges. Only load skills from sources you trust.
7. **Use `AGENTKTHX_PARALLEL_TOOLS=0`** if you want strict sequential tool execution. Parallel mode (default, 4 workers) is safe for independent batches but increases the blast radius of a misbehaving tool.
8. **Treat `shell()` as untrusted-by-default.** The denylist is best-effort. For genuinely untrusted input, prefer `safe_eval` or a custom tool with a tighter input contract.

---

## Contact

- Security reports: GitHub Security Advisories (preferred) or `veritas@vts-tech.org`
- General security questions: open a regular GitHub Discussion (not an issue)
- Security-relevant audit findings live in `audit/audit.md` — the register is ID-indexed (e.g., `SEC-09`, `ROB-33`) and is the canonical source

---

*This policy is versioned with the codebase. Material changes are noted in `docs/CHANGELOG.md`.*
