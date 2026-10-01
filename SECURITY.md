# Security Policy

## Supported versions

ProkName is in early development (`0.1.x`); only the latest commit on `main`
and the most recent tagged release receive security fixes.

## Reporting a vulnerability

Please use **GitHub's private vulnerability reporting** for anything that
could be exploited (Reports → "Report a vulnerability" on this repository).
This keeps the details confidential until a fix is released. If private
reporting is unavailable for any reason, open a regular issue **without
exploitation details** and say "security" in the title so it can be moved to
a private channel.

Please include: affected version or commit, reproduction steps, and impact.
You can expect an initial response within 7 days.

## Scope notes specific to this project

- **Credentials stay out of the repository.** LPSN credentials are supplied
  to CI only through repository Actions secrets (`LPSN_USER` /
  `LPSN_PASSWORD`). Do not file "the repo contains my password" reports
  without checking the commit history — but if you do find a real credential
  in history, report it immediately and we will rotate it.
- **Upstream data integrity** (LPSN / SeqCode / GNA responses differing from
  the pinned contracts in `tests/fixtures/`) is a correctness matter, not a
  security vulnerability: please open a normal issue for it.
- Reports about the research claims of the corpus or the benchmark are also
  regular issues.

## Known non-issues

- The CLI writes all output to the caller-selected locations; it does not
  elevate privileges or listen on network sockets. Install-time and runtime
  exposure is limited to the declared PyPI dependencies.

---

安全政策（中文版）见 [SECURITY.zh.md](SECURITY.zh.md)。
