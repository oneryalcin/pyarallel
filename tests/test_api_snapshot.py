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
from pathlib import Path

import pyarallel

SNAPSHOT = Path(__file__).parent / "api_snapshot.txt"


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
                lines.append(f"{name} (class)")
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
                            sig = str(inspect.signature(fn))
                        except (TypeError, ValueError):
                            sig = "(...)"
                        lines.append(f"  {name}.{attr}{sig}")
                    else:
                        lines.append(f"  {name}.{attr} (attribute)")
        elif callable(obj):
            try:
                sig = str(inspect.signature(obj))
            except (TypeError, ValueError):
                sig = "(...)"
            lines.append(f"{name}{sig}")
        else:
            lines.append(f"{name} = {obj!r}")
    return "\n".join(lines) + "\n"


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
