# Security Policy

## Reporting a vulnerability

Please report security issues **privately** via
[GitHub private vulnerability reporting](https://github.com/oneryalcin/pyarallel/security/advisories/new)
— do not open a public issue. You should receive a response within a
week.

## Scope notes

- **Checkpoint files contain pickle.** Loading a checkpoint executes
  what its pickle streams contain — this is documented, deliberate
  design, not a vulnerability: treat checkpoint files like code and
  never resume from a file you didn't create. New files are created
  `0o600` (POSIX) and symlinked paths fail closed. Reports about
  loading *attacker-supplied* checkpoint files will be closed as
  working-as-documented; reports about pyarallel creating files less
  safely than documented are very much in scope.
- Supported versions: the latest release.
