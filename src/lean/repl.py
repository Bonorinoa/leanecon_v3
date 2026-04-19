"""LeanInteract-backed REPL session helpers."""

from __future__ import annotations

import inspect
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.config import CACHE_DIR, LEAN_TIMEOUT, LEAN_WORKSPACE
from src.lean.compiler import compile_check

try:  # pragma: no cover - exercised via import fallback tests
    from lean_interact import AutoLeanServer, Command, LeanREPLConfig, LocalProject, ProofStep
    from lean_interact.interface import CommandResponse, LeanError, ProofStepResponse
    from lean_interact.project import get_project_lean_version
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError("lean_interact is not installed") from exc


def _replace_standalone_sorry(theorem_with_sorry: str, replacement: str) -> str:
    lines = theorem_with_sorry.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "sorry":
            continue
        indent = line[: len(line) - len(line.lstrip())] or "  "
        replacement_lines = [f"{indent}{part}" for part in replacement.splitlines()]
        return "\n".join(lines[:index] + replacement_lines + lines[index + 1 :])
    raise ValueError("Theorem does not contain a standalone `sorry` line.")


def _repl_cache_dir() -> Path:
    cache_dir = CACHE_DIR / "lean_interact"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _packaged_local_repl_path() -> Path | None:
    try:
        import lean_interact
    except ImportError:
        return None

    if not LEAN_WORKSPACE.exists():
        return None

    lean_version = get_project_lean_version(str(LEAN_WORKSPACE))
    if not lean_version:
        return None

    repl_rev = inspect.signature(LeanREPLConfig).parameters["repl_rev"].default
    package_cache = Path(lean_interact.__file__).resolve().parent / "cache"
    packaged_repl = (
        package_cache
        / "augustepoiroux"
        / "repl"
        / f"repl_{repl_rev}_lean-toolchain-{lean_version}"
    )
    if packaged_repl.exists():
        return packaged_repl
    return None


def _repair_repl_cache(cache_dir: Path) -> None:
    broken_clean_copy = cache_dir / "augustepoiroux" / "repl" / "repl_clean_copy"
    if broken_clean_copy.exists() and not (broken_clean_copy / ".git").exists():
        shutil.rmtree(broken_clean_copy, ignore_errors=True)


@lru_cache(maxsize=1)
def shared_repl_config() -> LeanREPLConfig:
    """Build and cache the LeanInteract config for the local workspace."""

    cache_dir = _repl_cache_dir()
    _repair_repl_cache(cache_dir)
    config_kwargs: dict[str, object] = {
        "project": LocalProject(directory=str(LEAN_WORKSPACE)),
        "cache_dir": cache_dir,
    }
    packaged_repl = _packaged_local_repl_path()
    if packaged_repl is not None:
        config_kwargs["local_repl_path"] = packaged_repl

    return LeanREPLConfig(
        **config_kwargs,
    )


@dataclass
class ProofSessionState:
    """Track the current proof state and tactic history for one theorem."""

    theorem_with_sorry: str
    proof_state: int
    goal: str
    goals: list[str] = field(default_factory=list)
    tactics: list[str] = field(default_factory=list)
    completed: bool = False

    @property
    def state_id(self) -> int:
        return self.proof_state

    @property
    def is_solved(self) -> bool:
        return self.completed or not self.goals

    def materialized_code(self) -> str:
        replacement = "\n".join(self.tactics) if self.tactics else "sorry"
        return _replace_standalone_sorry(self.theorem_with_sorry, replacement)


@dataclass
class TacticResult:
    """Compatibility wrapper for tactic execution results."""

    success: bool
    state_id: int
    goals: list[str] = field(default_factory=list)
    is_solved: bool = False
    proof_status: str = ""
    error: str | None = None


class LeanREPLSession:
    """Small session wrapper around one AutoLeanServer instance."""

    def __init__(self, *, timeout: float | None = LEAN_TIMEOUT):
        self.timeout = timeout
        self._server = AutoLeanServer(shared_repl_config())
        self._proof_state: ProofSessionState | None = None

    @property
    def proof_state(self) -> ProofSessionState | None:
        return self._proof_state

    def get_goal_state(self, state_id: int | None = None) -> ProofSessionState:
        if self._proof_state is None:
            raise RuntimeError("Call start_proof() before get_goal_state().")
        if state_id is not None and state_id != self._proof_state.state_id:
            raise RuntimeError("Requested proof state does not match the active REPL state.")
        return self._proof_state

    def run_command(
        self,
        command: str,
        *,
        env: int | None = None,
        timeout: float | None = None,
    ) -> CommandResponse | LeanError:
        return self._server.run(
            Command(cmd=command, env=env),
            timeout=self.timeout if timeout is None else timeout,
            add_to_session_cache=False,
        )

    def start_proof(
        self,
        theorem_with_sorry: str,
        *,
        timeout: float | None = None,
    ) -> ProofSessionState:
        response = self._server.run(
            Command(cmd=theorem_with_sorry),
            timeout=self.timeout if timeout is None else timeout,
            add_to_session_cache=True,
        )
        if isinstance(response, LeanError):
            raise RuntimeError(response.message)
        if not response.lean_code_is_valid(allow_sorry=True):
            errors = "; ".join(message.data for message in response.get_errors())
            raise RuntimeError(errors or "The theorem stub contains Lean errors.")
        if len(response.sorries) != 1:
            raise ValueError(
                f"Expected exactly one `sorry` in theorem stub, found {len(response.sorries)}."
            )
        sorry = response.sorries[0]
        if sorry.proof_state is None:
            raise RuntimeError("LeanInteract did not return a proof state for the theorem stub.")

        self._proof_state = ProofSessionState(
            theorem_with_sorry=theorem_with_sorry,
            proof_state=sorry.proof_state,
            goal=sorry.goal,
            goals=[sorry.goal] if sorry.goal else [],
        )
        return self._proof_state

    def apply_tactic(
        self,
        *args: Any,
        timeout: float | None = None,
    ) -> ProofStepResponse | LeanError:
        if self._proof_state is None:
            raise RuntimeError("Call start_proof() before apply_tactic().")

        if len(args) == 1:
            state_id = self._proof_state.state_id
            tactic = str(args[0])
        elif len(args) == 2:
            state_id = int(args[0])
            tactic = str(args[1])
        else:
            raise TypeError("apply_tactic() expects tactic or (state_id, tactic).")

        if state_id != self._proof_state.state_id:
            raise RuntimeError("Requested proof state does not match the active REPL state.")

        response = self._server.run(
            ProofStep(proof_state=self._proof_state.proof_state, tactic=tactic),
            timeout=self.timeout if timeout is None else timeout,
            add_to_session_cache=True,
        )
        if isinstance(response, LeanError):
            return response
        if response.has_errors():
            return response

        self._proof_state.proof_state = response.proof_state
        self._proof_state.goal = "\n\n".join(response.goals)
        self._proof_state.goals = list(response.goals)
        self._proof_state.tactics.append(tactic)
        self._proof_state.completed = response.proof_status == "Completed"
        return response

    def materialize_proof(self) -> str:
        if self._proof_state is None:
            raise RuntimeError("No active proof to materialize.")
        return self._proof_state.materialized_code()

    def verify_materialized_proof(
        self,
        *,
        filename: str = "repl_verified.lean",
        timeout: int | None = None,
    ) -> dict:
        code = self.materialize_proof()
        compile_timeout = LEAN_TIMEOUT if timeout is None else timeout
        return compile_check(code, timeout=compile_timeout, filename=filename)

    def kill(self) -> None:
        self._server.kill()

    def __enter__(self) -> "LeanREPLSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.kill()
        return False


LeanREPL = LeanREPLSession
