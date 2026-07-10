"""The public-API snapshot gate (v0.10): an accidental change to the
exported surface must be a red build, not a surprise in someone's
upgrade.

How it works: this test renders every ``pyarallel`` export — names,
signatures, public methods, enum members — into canonical lines and
compares them against the committed ``tests/api_snapshot.txt``.

Changing the API deliberately? Regenerate the snapshot and commit it —
the diff then shows the change explicitly in review:

    uv run python tests/test_api_snapshot.py > tests/api_snapshot.txt
"""

import enum
import inspect
import re
from pathlib import Path

import pyarallel

SNAPSHOT = Path(__file__).parent / "api_snapshot.txt"


_ADDR = re.compile(r"<object object at 0x[0-9a-f]+>")


def _stable(sig: str) -> str:
    """Sentinel defaults repr with a memory address — normalize, or the
    snapshot differs on every interpreter run."""
    return _ADDR.sub("<sentinel>", sig)


def render_api() -> str:
    lines: list[str] = []
    for name in sorted(pyarallel.__all__):
        obj = getattr(pyarallel, name)
        if inspect.isclass(obj):
            if issubclass(obj, enum.Enum):
                members = ", ".join(m.name for m in obj)
                lines.append(f"{name} (enum): {members}")
            elif issubclass(obj, BaseException):
                lines.append(
                    f"{name} (exception, bases: "
                    f"{', '.join(b.__name__ for b in obj.__bases__)})"
                )
            else:
                # Constructor signature IS public surface (v0.10 review:
                # omitting it let a breaking Retry(...) change pass CI).
                try:
                    ctor = _stable(str(inspect.signature(obj)))
                except (TypeError, ValueError):
                    ctor = "(...)"
                lines.append(f"{name} (class){ctor}")
                for attr in sorted(vars(obj)):
                    if attr.startswith("_"):
                        continue
                    member = inspect.getattr_static(obj, attr)
                    if isinstance(member, property):
                        lines.append(f"  {name}.{attr} (property)")
                    elif callable(member) or isinstance(
                        member, (classmethod, staticmethod)
                    ):
                        fn = getattr(obj, attr)
                        try:
                            sig = _stable(str(inspect.signature(fn)))
                        except (TypeError, ValueError):
                            sig = "(...)"
                        lines.append(f"  {name}.{attr}{sig}")
                    else:
                        lines.append(f"  {name}.{attr} (attribute)")
        elif callable(obj):
            try:
                sig = _stable(str(inspect.signature(obj)))
            except (TypeError, ValueError):
                sig = "(...)"
            lines.append(f"{name}{sig}")
        else:
            lines.append(f"{name} = {obj!r}")

    # The decorator per-call option surfaces are documented API too —
    # the TypedDicts aren't in __all__ but their keys are the contract
    # behind .map()/.starmap()/.stream() kwargs.
    from pyarallel import aio as _aio
    from pyarallel import core as _core

    for td_name in (
        "SyncMapOptions",
        "SyncStarmapOptions",
        "SyncStreamOptions",
        "AsyncMapOptions",
        "AsyncStarmapOptions",
        "AsyncStreamOptions",
    ):
        td = getattr(_core, td_name, None) or getattr(_aio, td_name)
        keys = ", ".join(sorted(td.__annotations__))
        lines.append(f"{td_name} (options): {keys}")
    return "\n".join(lines) + "\n"


def test_constructor_mutation_trips_the_gate(monkeypatch):
    """Regression for the gate itself: a changed constructor signature
    must change the rendering (the v1 snapshot listed classes without
    constructors, so a breaking Retry(...) change passed CI)."""
    baseline = render_api()
    assert "Retry (class)(" in baseline  # constructors are rendered

    class MutatedRetry:
        def __init__(self, totally_new_arg: int) -> None: ...

    monkeypatch.setattr(pyarallel, "Retry", MutatedRetry)
    assert render_api() != baseline


def test_public_api_matches_snapshot():
    """Prevents: a rename, a signature change, a dropped export, or a
    new default sneaking into a release without a reviewed snapshot
    diff. Regenerate deliberately (see module docstring)."""
    current = render_api()
    committed = SNAPSHOT.read_text()
    assert current == committed, (
        "Public API changed. If intentional, regenerate the snapshot:\n"
        "  uv run python tests/test_api_snapshot.py > tests/api_snapshot.txt\n"
        "and commit the diff."
    )


if __name__ == "__main__":
    print(render_api(), end="")
