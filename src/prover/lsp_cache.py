"""Per-prove LSP outline/hover cache and LeanSearch premise enrichment.

Extracted from ``src/prover/prover.py`` in Sprint 24 so the enrichment loop
can be tested in isolation. The cache is intentionally per-``Prover.prove``
invocation (cleared in the prover's budget reset) so it cannot grow across
unrelated targets.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol


class _LSPClient(Protocol):
    def lean_file_outline(self, module: str) -> Any: ...
    def lean_hover_info(self, module: str, *, line: int, column: int) -> Any: ...


ErrorCallback = Callable[[str, BaseException, str], None]


def _coerce_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class LSPCache:
    """Cache outlines/hovers for one prove invocation and enrich premises."""

    def __init__(
        self,
        lsp_client: _LSPClient,
        *,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self.lsp_client = lsp_client
        self._on_error = on_error or (lambda _tool, _exc, _ctx: None)
        self.outlines: dict[str, list[dict[str, Any]]] = {}
        self.hovers: dict[tuple[str, int, int], str] = {}

    # ---- lifecycle ---------------------------------------------------------

    def clear(self) -> None:
        self.outlines.clear()
        self.hovers.clear()

    # ---- LSP-backed lookups ------------------------------------------------

    def get_outline(self, module: str) -> list[dict[str, Any]]:
        cached = self.outlines.get(module)
        if cached is not None:
            return cached
        decls: list[dict[str, Any]] = []
        try:
            payload = self.lsp_client.lean_file_outline(module)
            decls = list(
                (payload or {}).get("declarations")
                or (payload or {}).get("items")
                or []
            )
        except Exception as exc:
            self._on_error("lean_file_outline", exc, module)
            decls = []
        self.outlines[module] = decls
        return decls

    def get_hover(self, module: str, line: int, column: int) -> str:
        key = (module, int(line), int(column))
        if key in self.hovers:
            return self.hovers[key]
        text = ""
        try:
            payload = self.lsp_client.lean_hover_info(module, line=line, column=column)
            if isinstance(payload, dict):
                contents = payload.get("contents") or payload.get("value")
                if isinstance(contents, list):
                    text = "\n".join(str(c) for c in contents if c)
                elif contents is not None:
                    text = str(contents)
            elif isinstance(payload, str):
                text = payload
        except Exception as exc:
            self._on_error("lean_hover_info", exc, f"{module}:{line}:{column}")
            text = ""
        self.hovers[key] = text
        return text

    # ---- decl matching -----------------------------------------------------

    @staticmethod
    def find_decl(
        outline: list[dict[str, Any]], name: str
    ) -> dict[str, Any] | None:
        for decl in outline:
            if str(decl.get("name") or "") == name:
                return decl
        # Qualified→unqualified suffix match only. Earlier bidirectional
        # matching could pick the wrong overload (e.g. ``foo`` matching
        # unrelated ``Bar.foo``).
        suffix = "." + name
        for decl in outline:
            decl_name = str(decl.get("name") or "")
            if decl_name and decl_name.endswith(suffix):
                return decl
        return None

    # ---- enrichment --------------------------------------------------------

    def enrich_premises(self, premises: list[dict[str, Any]]) -> int:
        """Populate full_type_signature/detailed_docstring/declaration_location.

        Mutates *premises* in place. Returns the number successfully enriched.
        """
        enriched = 0
        for premise in premises:
            module = premise.get("file_path")
            name = premise.get("name")
            if not module or not name:
                continue
            outline = self.get_outline(module)
            decl = self.find_decl(outline, name)
            if decl is None:
                continue
            line = _coerce_int(decl.get("line"))
            column = _coerce_int(decl.get("column"), default=0)
            if line is None:
                continue
            hover_text = self.get_hover(module, line, column)
            if not hover_text:
                continue
            premise["full_type_signature"] = hover_text
            premise["detailed_docstring"] = hover_text
            premise["declaration_location"] = f"{module}:{line}"
            enriched += 1
        return enriched


__all__ = ["LSPCache"]
