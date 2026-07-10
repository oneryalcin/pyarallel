# Compatibility & Deprecation Policies

What you can rely on across pyarallel versions — stated before 1.0, so
the freeze means something. These policies bind from **1.0.0** onward;
the pre-1.0 sections describe the current reality honestly.

## Versioning

Pyarallel follows [semantic versioning](https://semver.org/) from 1.0.0:

- **Patch** (`1.0.x`): bug fixes only. No API surface change — enforced
  in CI by the [public-API snapshot gate](#the-api-snapshot-gate).
- **Minor** (`1.x.0`): additive only. New parameters get defaults; new
  exports don't collide; nothing existing changes meaning.
- **Major** (`x.0.0`): the only place breaking changes may live, each
  with a CHANGELOG migration note.

**Pre-1.0** (now): breaking changes ship in minor versions, deliberately
batched (v0.8 was one planned contract-breaking release) and always with
prominent CHANGELOG migration notes. Pinning `pyarallel~=0.x.0` per
minor is the safe spelling until 1.0.

## Deprecation (from 1.0.0)

- A deprecated API emits `DeprecationWarning` for **at least one minor
  release** before removal, and removal happens only in a major.
- The warning message names the replacement and the removal version.
- The CHANGELOG lists every active deprecation in each release's notes.

## Checkpoint file guarantees

Checkpoint files are the one on-disk artifact pyarallel owns. The
contract:

- **Schema stability**: files are stamped with a schema version
  (currently `2`). A pyarallel release reads every schema it documents
  supporting and **fails closed** — with delete-to-start-fresh
  instructions — on any schema it doesn't. There is no silent migration
  and no silent adoption of unrecognized files.
- **Within a major** (from 1.0.0): checkpoint files written by version
  `X.a` are readable by `X.b` for any `b >= a`. Reading newer files
  with older code is not guaranteed (example: 0.8 readers don't enforce
  the `checkpoint_version=` fence that 0.9 writers record).
- **Across majors**: readable unless the CHANGELOG says otherwise; a
  schema bump fails closed rather than misreading.
- **Pickle boundary**: rows are pickle. That means (a) results must be
  picklable, (b) a checkpoint is only usable by code that can import
  the pickled types, and (c) **a checkpoint file is code — never resume
  from a file you didn't create** (see the
  [security note](../user-guide/advanced-features.md#checkpoint-resume)).
- **Python version**: pickled with the running interpreter's default
  protocol; files move across Python versions within pickle's own
  compatibility rules (protocol 5 reads on every supported Python).

## Python version support

- The floor is declared in `requires-python` (currently **3.12**) and
  every supported version — including free-threaded builds and the
  3.14+ interpreter executor — is exercised in CI per commit.
- A floor raise is a **minor** release pre-1.0 and a **major** from
  1.0.0, announced one release ahead in the CHANGELOG.
- New Python versions are added to CI when their first beta ships;
  support is claimed only after the full matrix is green.

## The API snapshot gate

`tests/api_snapshot.txt` is a committed, human-readable rendering of the
entire public surface — every export, signature, method, property, and
enum member. CI compares the live API against it on every push, so an
accidental rename, signature change, or dropped export is a **red
build**, and a deliberate one is a **reviewed diff**. Regenerating it is
a conscious act:

```bash
uv run python tests/test_api_snapshot.py > tests/api_snapshot.txt
```

## What "public" means

The public API is exactly `pyarallel.__all__` and the documented
parameters of those objects. Anything prefixed `_`, any module not
re-exported from the package root, and the checkpoint file's internal
layout (beyond the guarantees above) are implementation details and may
change without notice.
