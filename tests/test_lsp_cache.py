"""Tests for the per-prove LSP outline/hover cache and enrichment loop."""

from __future__ import annotations

from typing import Any

from src.prover.lsp_cache import LSPCache


class _StubClient:
    def __init__(
        self,
        *,
        outlines: dict[str, Any] | None = None,
        hovers: dict[tuple[str, int, int], Any] | None = None,
        outline_exc: BaseException | None = None,
        hover_exc: BaseException | None = None,
    ) -> None:
        self.outlines = outlines or {}
        self.hovers = hovers or {}
        self._outline_exc = outline_exc
        self._hover_exc = hover_exc
        self.outline_calls: list[str] = []
        self.hover_calls: list[tuple[str, int, int]] = []

    def lean_file_outline(self, module: str) -> Any:
        self.outline_calls.append(module)
        if self._outline_exc:
            raise self._outline_exc
        return self.outlines.get(module)

    def lean_hover_info(self, module: str, *, line: int, column: int) -> Any:
        self.hover_calls.append((module, line, column))
        if self._hover_exc:
            raise self._hover_exc
        return self.hovers.get((module, line, column))


def test_get_outline_caches_payload():
    client = _StubClient(
        outlines={"M.A": {"declarations": [{"name": "M.A.foo", "line": 1, "column": 0}]}}
    )
    cache = LSPCache(client)

    decls = cache.get_outline("M.A")
    decls_again = cache.get_outline("M.A")

    assert decls == [{"name": "M.A.foo", "line": 1, "column": 0}]
    assert decls_again is decls
    assert client.outline_calls == ["M.A"], "second lookup must not re-hit LSP"


def test_get_outline_handles_items_alias():
    client = _StubClient(outlines={"M.B": {"items": [{"name": "M.B.x"}]}})
    cache = LSPCache(client)
    assert cache.get_outline("M.B") == [{"name": "M.B.x"}]


def test_get_outline_invokes_on_error_and_returns_empty():
    captured: list[tuple[str, BaseException, str]] = []
    client = _StubClient(outline_exc=RuntimeError("LSP down"))
    cache = LSPCache(client, on_error=lambda *args: captured.append(args))

    assert cache.get_outline("M.C") == []
    # Subsequent calls must use the cached empty result, not retry.
    assert cache.get_outline("M.C") == []
    assert client.outline_calls == ["M.C"]
    assert captured and captured[0][0] == "lean_file_outline"
    assert captured[0][2] == "M.C"


def test_get_hover_normalizes_payload_shapes():
    client = _StubClient(
        hovers={
            ("M.A", 1, 0): {"contents": ["sig", "doc"]},
            ("M.A", 2, 0): {"value": "v"},
            ("M.A", 3, 0): "raw text",
            ("M.A", 4, 0): None,
        }
    )
    cache = LSPCache(client)

    assert cache.get_hover("M.A", 1, 0) == "sig\ndoc"
    assert cache.get_hover("M.A", 2, 0) == "v"
    assert cache.get_hover("M.A", 3, 0) == "raw text"
    assert cache.get_hover("M.A", 4, 0) == ""


def test_get_hover_caches_and_swallows_errors():
    captured: list[tuple[str, BaseException, str]] = []
    client = _StubClient(hover_exc=RuntimeError("hover broken"))
    cache = LSPCache(client, on_error=lambda *args: captured.append(args))

    assert cache.get_hover("M.A", 5, 0) == ""
    assert cache.get_hover("M.A", 5, 0) == ""
    assert client.hover_calls == [("M.A", 5, 0)]
    assert captured and captured[0][0] == "lean_hover_info"


def test_find_decl_exact_match_wins_over_suffix():
    outline = [
        {"name": "Foo.bar"},
        {"name": "bar"},
    ]
    # Exact match returns the second entry, not the qualified one.
    assert LSPCache.find_decl(outline, "bar") == {"name": "bar"}


def test_find_decl_qualified_suffix_matches_only_with_dot():
    outline = [
        {"name": "Foo.bar"},
        {"name": "Other.barbaz"},
    ]
    assert LSPCache.find_decl(outline, "bar") == {"name": "Foo.bar"}
    assert LSPCache.find_decl(outline, "baz") is None


def test_enrich_premises_populates_signature_and_location():
    client = _StubClient(
        outlines={"M.X": {"declarations": [{"name": "M.X.foo", "line": 7, "column": 4}]}},
        hovers={("M.X", 7, 4): {"contents": "M.X.foo : Nat"}},
    )
    cache = LSPCache(client)
    premises = [{"name": "foo", "file_path": "M.X", "score": 0.8}]

    enriched = cache.enrich_premises(premises)

    assert enriched == 1
    assert premises[0]["full_type_signature"] == "M.X.foo : Nat"
    assert premises[0]["detailed_docstring"] == "M.X.foo : Nat"
    assert premises[0]["declaration_location"] == "M.X:7"


def test_enrich_premises_skips_when_outline_missing_decl():
    client = _StubClient(
        outlines={"M.X": {"declarations": [{"name": "M.X.other", "line": 1, "column": 0}]}}
    )
    cache = LSPCache(client)
    premises = [{"name": "missing", "file_path": "M.X"}]

    assert cache.enrich_premises(premises) == 0
    assert "full_type_signature" not in premises[0]


def test_enrich_premises_ignores_premises_without_module_or_name():
    client = _StubClient()
    cache = LSPCache(client)
    premises = [{"name": "foo"}, {"file_path": "M.X"}, {}]
    assert cache.enrich_premises(premises) == 0
    assert client.outline_calls == []


def test_clear_drops_both_caches():
    client = _StubClient(
        outlines={"M.X": {"declarations": []}},
        hovers={("M.X", 1, 0): "h"},
    )
    cache = LSPCache(client)
    cache.get_outline("M.X")
    cache.get_hover("M.X", 1, 0)
    assert cache.outlines and cache.hovers

    cache.clear()
    assert cache.outlines == {} and cache.hovers == {}
