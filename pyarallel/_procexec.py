"""Out-of-context executor support: making user functions importable in workers.

Serves both executors whose workers can't share the caller's objects —
process and interpreter. Decorated top-level functions are no longer the
module-global binding, so they can't be pickled directly. We ship
(module, qualname) instead and re-import inside the worker.
"""

from __future__ import annotations

import functools
import importlib
from collections.abc import Callable
from typing import Any, cast


def _resolve_process_target(
    fn: Callable[..., Any],
) -> tuple[str, str] | None:
    """Return a module/qualname pair when *fn* can be re-imported in a worker.

    This keeps process execution working for decorated top-level functions,
    whose original function object is no longer the module-global binding.
    """
    if getattr(fn, "__self__", None) is not None:
        return None

    module_name = getattr(fn, "__module__", None)
    qualname = getattr(fn, "__qualname__", None)
    if not module_name or not qualname or "<locals>" in qualname:
        return None
    return (module_name, qualname)


@functools.cache
def _load_process_target(module_name: str, qualname: str) -> Callable[..., Any]:
    """Import and resolve a callable by module and qualname."""
    target: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise TypeError(f"{module_name}.{qualname} is not callable")
    return cast(Callable[..., Any], target)


def _call_resolved(item: Any, *, module_name: str, qualname: str) -> Any:
    """Resolve a callable inside the worker process and call it with one item."""
    return _load_process_target(module_name, qualname)(item)


def _call_resolved_args(
    args: tuple[Any, ...], *, module_name: str, qualname: str
) -> Any:
    """Resolve a callable inside the worker process and call it with *args."""
    return _load_process_target(module_name, qualname)(*args)
