"""Proof file management helpers for the proving harness."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from src.config import LEAN_PROOF_DIR


_THEOREM_RE = re.compile(r"(?m)^(theorem|lemma)\s+([A-Za-z0-9_']+)\b")
_BODY_MARKER = ":= by"


def _theorem_match(code: str, theorem_name: str | None = None) -> re.Match[str] | None:
    match = _THEOREM_RE.search(code)
    if theorem_name is None or match is None or match.group(2) == theorem_name:
        return match
    for candidate in _THEOREM_RE.finditer(code):
        if candidate.group(2) == theorem_name:
            return candidate
    return None


@dataclass
class ProofFileController:
    """Manage proof file paths and deterministic checkpoint names."""

    workspace_root: Path = LEAN_PROOF_DIR
    checkpoint_root: Path | None = None
    authoritative_stubs: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.checkpoint_root is None:
            self.checkpoint_root = self.workspace_root / "checkpoints"
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.checkpoint_root.mkdir(parents=True, exist_ok=True)

    def proof_path(self, job_id: str) -> Path:
        """Return the canonical proof file path for a job."""

        return self.workspace_root / f"{job_id}.lean"

    def checkpoint_path(self, job_id: str, step: int) -> Path:
        """Return the checkpoint file path for a proving step."""

        assert self.checkpoint_root is not None
        return self.checkpoint_root / f"{job_id}_{step:03d}.lean"

    def initialize(self, job_id: str, theorem_with_sorry: str) -> Path:
        """Create the working Lean file for one verification job."""

        self.authoritative_stubs[job_id] = theorem_with_sorry
        path = self.proof_path(job_id)
        path.write_text(theorem_with_sorry, encoding="utf-8")
        return path

    def read_current_code(self, job_id: str) -> str:
        """Read the current working file for a job."""

        return self.proof_path(job_id).read_text(encoding="utf-8")

    def write_current_code(self, job_id: str, theorem_code: str) -> Path:
        """Overwrite the current working file for a job."""

        path = self.proof_path(job_id)
        path.write_text(self.build_final_code(job_id, theorem_code), encoding="utf-8")
        return path

    def build_final_code(self, job_id: str, theorem_code: str) -> str:
        """Reattach the authoritative theorem context around a materialized proof body."""

        stub = self.authoritative_stubs.get(job_id)
        stub_match = _theorem_match(stub or "")
        if stub is None or stub_match is None:
            return theorem_code
        stub_body = stub.find(_BODY_MARKER, stub_match.end())
        code_match = _theorem_match(theorem_code, stub_match.group(2))
        code_body = theorem_code.find(_BODY_MARKER, code_match.end()) if code_match is not None else -1
        if stub_body == -1 or code_body == -1:
            return theorem_code
        context = stub
        proof = self.proof_path(job_id)
        if proof.exists():
            current = proof.read_text(encoding="utf-8")
            current_match = _theorem_match(current, stub_match.group(2))
            if current_match is not None:
                context = current
        if code_match is not None and code_match.start() > 0:
            context = theorem_code
        context_match = _theorem_match(context, stub_match.group(2))
        if context_match is None:
            return theorem_code
        body = theorem_code[code_body + len(_BODY_MARKER) :]
        rebuilt = (
            context[: context_match.start()]
            + stub[stub_match.start() : stub_body + len(_BODY_MARKER)]
            + body
        )
        return rebuilt if rebuilt.endswith("\n") else rebuilt + "\n"

    def checkpoint(self, job_id: str, step: int) -> Path:
        """Snapshot the current working file to a deterministic checkpoint."""

        current = self.proof_path(job_id)
        checkpoint = self.checkpoint_path(job_id, step)
        checkpoint.write_text(current.read_text(encoding="utf-8"), encoding="utf-8")
        return checkpoint

    def scratch_path(self, prefix: str = "ScratchProof") -> Path:
        """Return a unique auxiliary Lean file path inside the workspace."""

        return self.workspace_root / f"{prefix}_{uuid4().hex[:12]}.lean"

    def cleanup(self, job_id: str) -> None:
        """Delete the job's working file and all its checkpoints."""

        self.authoritative_stubs.pop(job_id, None)
        proof = self.proof_path(job_id)
        try:
            proof.unlink(missing_ok=True)
        except OSError:
            pass

        assert self.checkpoint_root is not None
        for cp in self.checkpoint_root.glob(f"{job_id}_*.lean"):
            try:
                cp.unlink(missing_ok=True)
            except OSError:
                pass
