from __future__ import annotations

import pytest

from src.guardrails import (
    extract_claim_frame,
    extract_stub_frame,
    get_type_signature,
    is_vacuous,
    semantic_faithfulness_score,
    validate_identifiers,
    vacuity_report,
)


def test_vacuity_detection_for_true_scaffold() -> None:
    theorem = "theorem demo : True := by\n  sorry\n"
    assert is_vacuous(theorem) is True
    assert vacuity_report(theorem)["is_vacuous"] is True


def test_claim_and_stub_frames_capture_game_content() -> None:
    claim = "A Nash equilibrium exists in a finite game."
    stub = "import LeanEcon.Preamble.GameTheory.NashEquilibriumPure\n\ntheorem t : True := by\n  sorry\n"

    claim_frame = extract_claim_frame(claim)
    stub_frame = extract_stub_frame(stub)

    assert "game" in claim_frame.concepts
    assert "LeanEcon.Preamble.GameTheory.NashEquilibriumPure" in stub_frame.imports


def test_semantic_faithfulness_requires_review_in_mid_band() -> None:
    result = semantic_faithfulness_score(
        "The Bellman equation defines the value function.",
        "import LeanEcon.Preamble.Dynamic.BellmanEquation\n\ntheorem bellman_claim : True := by\n  sorry\n",
    )

    assert result["score"] >= 4.0
    assert result["needs_human_review"] is True
    assert result["passes_gate"] is False


def test_semantic_frame_existential_branch() -> None:
    result = semantic_faithfulness_score(
        "There exists an equilibrium.",
        "theorem exists_eq : True := by\n  sorry\n",
    )
    assert result["claim_frame"]["quantifier_shape"] == "existential"


@pytest.mark.anyio
async def test_repl_validator_falls_back_when_disabled(monkeypatch) -> None:
    import src.guardrails.repl_validator as rv

    monkeypatch.setattr(rv, "FORMALIZER_REPL_VALIDATION_ENABLED", False)
    assert await validate_identifiers(["Nat.add"], ["Mathlib"]) == {}
    assert await get_type_signature("Nat.add", ["Mathlib"]) is None
    assert await validate_identifiers([], []) == {}


@pytest.mark.anyio
async def test_repl_validator_uses_fake_session(monkeypatch) -> None:
    import src.guardrails.repl_validator as rv

    monkeypatch.setattr(rv, "FORMALIZER_REPL_VALIDATION_ENABLED", True)
    monkeypatch.setattr(rv, "_LEAN_INTERACT_AVAILABLE", True)

    class FakeResponse:
        def __init__(self, valid: bool, message: str = "") -> None:
            self._valid = valid
            self.messages = [type("Msg", (), {"severity": "info", "data": message})]

        def lean_code_is_valid(self):
            return self._valid

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def run_command(self, command: str):
            if command.endswith("#print Nat.add"):
                return FakeResponse(True, "Nat.add : Nat -> Nat -> Nat")
            return FakeResponse(True)

    monkeypatch.setattr(rv, "_LeanREPLSession", FakeSession)
    monkeypatch.setattr(rv, "_LeanError", type(None))

    result = await validate_identifiers(["Nat.add"], ["Mathlib"])
    signature = await get_type_signature("Nat.add", ["Mathlib"])

    assert result == {"Nat.add": True}
    assert signature == "Nat.add : Nat -> Nat -> Nat"


@pytest.mark.anyio
async def test_repl_validator_handles_importless_and_lean_error(monkeypatch) -> None:
    import src.guardrails.repl_validator as rv

    monkeypatch.setattr(rv, "FORMALIZER_REPL_VALIDATION_ENABLED", True)
    monkeypatch.setattr(rv, "_LEAN_INTERACT_AVAILABLE", True)

    class FakeLeanError:
        pass

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def run_command(self, command: str):
            if command == "#check missing":
                return FakeLeanError()
            return type("Response", (), {"lean_code_is_valid": lambda self: True, "messages": []})()

    monkeypatch.setattr(rv, "_LeanREPLSession", FakeSession)
    monkeypatch.setattr(rv, "_LeanError", FakeLeanError)

    assert await validate_identifiers(["missing"], []) == {"missing": False}
    assert await get_type_signature("exists", []) is None

    class LeanErrorSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def run_command(self, command: str):
            return FakeLeanError()

    monkeypatch.setattr(rv, "_LeanREPLSession", LeanErrorSession)
    assert await get_type_signature("lean_error", []) is None
