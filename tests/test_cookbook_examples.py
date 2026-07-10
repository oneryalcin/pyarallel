"""Executable documentation (v0.10): the cookbook must not drift.

Production bug this prevents — and it already happened once: the
`batch_size` → `window_size` rename left every doc page correct only
because a human sweep caught them all. Prose examples rot silently;
these gates make rot a red build.

Three gates, cheapest first:

1. **Compile**: every ```python block in the user-facing docs parses
   (top-level ``await`` allowed — it's honest pedagogy; markdown
   indentation dedented). A block that is deliberately not executable
   Python (signature displays) opts out with a
   ``<!-- docs-check: skip -->`` comment on the line before the fence.
2. **API shape**: every call to a pyarallel export in every block is
   AST-checked — keyword arguments must exist in the real signature.
   This is the gate that would have caught ``batch_size=`` surviving
   in a recipe after the rename.
3. **Execute**: pages whose dependencies can be faked cheaply run end
   to end, block by block, in one namespace — the flagship LLM recipe
   (fake ``openai`` module) and the streaming ETL page (fake cursor).
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from pathlib import Path
from typing import Any

import pytest

import pyarallel

DOCS_ROOT = Path(__file__).parent.parent
SKIP_MARKER = "<!-- docs-check: skip -->"
FENCE = re.compile(r"(?:^|\n)([^\n]*)\n```python\n(.*?)```", re.DOTALL)

# User-facing pages only — development plans quote historical APIs.
PAGES = sorted(
    p for p in (DOCS_ROOT / "docs").rglob("*.md") if "development" not in p.parts
) + [DOCS_ROOT / "README.md"]


def _blocks(page: Path) -> list[tuple[int, str]]:
    """(index, source) for each non-skipped python block on the page."""
    out = []
    for i, match in enumerate(FENCE.finditer(page.read_text())):
        preceding_line, body = match.group(1), match.group(2)
        if SKIP_MARKER in preceding_line:
            continue
        out.append((i, textwrap.dedent(body)))
    return out


_ALL_CASES = [
    pytest.param(page, idx, src, id=f"{page.name}#{idx}")
    for page in PAGES
    for idx, src in _blocks(page)
]


@pytest.mark.parametrize(("page", "idx", "src"), _ALL_CASES)
def test_block_compiles(page: Path, idx: int, src: str) -> None:
    compile(src, f"{page.name}#{idx}", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


# --- Gate 2: keyword arguments against the real signatures -----------------

_API: dict[str, Any] = {
    name: getattr(pyarallel, name)
    for name in pyarallel.__all__
    if callable(getattr(pyarallel, name))
}
_ATTR_API = {("Retry", "for_http"): pyarallel.Retry.for_http}


def _signature_params(fn: Any) -> set[str] | None:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    if any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values()):
        return None  # **kwargs: anything goes
    return set(sig.parameters)


@pytest.mark.parametrize(("page", "idx", "src"), _ALL_CASES)
def test_pyarallel_kwargs_are_real(page: Path, idx: int, src: str) -> None:
    """Every keyword passed to a pyarallel callable in the docs must
    exist in its real signature — stale kwargs are how renamed options
    linger in examples."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        pytest.skip("top-level await block — covered by the compile gate")
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = None
        label = ""
        if isinstance(node.func, ast.Name) and node.func.id in _API:
            fn, label = _API[node.func.id], node.func.id
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and (node.func.value.id, node.func.attr) in _ATTR_API
        ):
            key = (node.func.value.id, node.func.attr)
            fn, label = _ATTR_API[key], ".".join(key)
        if fn is None:
            continue
        params = _signature_params(fn)
        if params is None:
            continue
        for kw in node.keywords:
            if kw.arg is not None and kw.arg not in params:
                problems.append(f"{label}(... {kw.arg}=...) — no such parameter")
    assert not problems, f"{page.name}#{idx}: " + "; ".join(problems)


# --- Gate 3: execute the feasible pages against fakes ----------------------


class _FakeResponse:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


class _FakeOpenAIError(Exception):
    def __init__(self) -> None:
        super().__init__("fake")
        self.response = _FakeResponse()


def _fake_openai_module() -> Any:
    import types

    mod = types.ModuleType("openai")
    mod.RateLimitError = type("RateLimitError", (_FakeOpenAIError,), {})
    mod.APIConnectionError = type("APIConnectionError", (Exception,), {})
    mod.InternalServerError = type("InternalServerError", (_FakeOpenAIError,), {})

    class _Completions:
        def create(self, **kwargs: Any) -> Any:
            import types as _t

            msg = _t.SimpleNamespace(content="billing")
            return _t.SimpleNamespace(choices=[_t.SimpleNamespace(message=msg)])

    class _Chat:
        completions = _Completions()

    class OpenAI:
        chat = _Chat()

    mod.OpenAI = OpenAI
    return mod


def _run_page(page: Path, namespace: dict[str, Any]) -> int:
    """Execute every sync block of *page* in one shared namespace."""
    ran = 0
    for idx, src in _blocks(page):
        tree = ast.parse(src, f"{page.name}#{idx}")
        if any(
            isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith))
            for n in ast.walk(tree)
        ):
            continue  # async narrative blocks: compile-gated only
        exec(compile(tree, f"{page.name}#{idx}", "exec"), namespace)  # noqa: S102
        ran += 1
    return ran


def test_llm_batch_calls_recipe_executes(monkeypatch, tmp_path):
    """The flagship recipe runs end to end against a fake openai module —
    the exact doc code, not a corrected paraphrase (the v0.7 cookbook
    review found dead code precisely because a verify script tested
    corrected shapes)."""
    import sys

    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module())
    monkeypatch.chdir(tmp_path)  # the recipe writes classify.ckpt
    saved: list[tuple[str, str]] = []
    ns: dict[str, Any] = {
        "tickets": ["t1", "t2", "t3"],
        "save": lambda t, label: saved.append((t, label)),
        "log_failed": lambda t, exc: pytest.fail(f"recipe failed on {t}: {exc}"),
    }
    ran = _run_page(DOCS_ROOT / "docs/cookbook/llm-batch-calls.md", ns)
    assert ran >= 1
    assert saved == [("t1", "billing"), ("t2", "billing"), ("t3", "billing")]


def test_streaming_etl_recipe_executes():
    """The streaming page's pipeline runs against a fake cursor."""
    import datetime

    rows = [
        {
            "id": i,
            "created_at": datetime.datetime(2026, 1, 1) + datetime.timedelta(hours=i),
            "raw_data": '{"k": 1}',
        }
        for i in range(25)
    ]

    class FakeCursor:
        def __init__(self) -> None:
            self._remaining: list[dict[str, Any]] = []

        def execute(self, query: str) -> None:
            self._remaining = list(rows)

        def fetchmany(self, n: int) -> list[dict[str, Any]]:
            batch, self._remaining = self._remaining[:n], self._remaining[n:]
            return batch

    written: list[list[Any]] = []
    ns: dict[str, Any] = {
        "cursor": FakeCursor(),
        "write_parquet_batch": lambda buf: written.append(list(buf)),
        "log_error": lambda idx, exc: pytest.fail(f"etl failed at {idx}: {exc}"),
    }
    ran = _run_page(DOCS_ROOT / "docs/cookbook/streaming-etl.md", ns)
    assert ran >= 1
    total = sum(len(b) for b in written) + len(ns.get("output_buffer", []))
    assert total == 25  # every row transformed, none lost
