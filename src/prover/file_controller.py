"""Proof file management helpers for the proving harness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from src.config import LEAN_PROOF_DIR


@dataclass
class ProofFileController:
    """Manage proof file paths and deterministic checkpoint names."""

    workspace_root: Path = LEAN_PROOF_DIR
    checkpoint_root: Path | None = None

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

        path = self.proof_path(job_id)
        path.write_text(theorem_with_sorry, encoding="utf-8")
        return path

    def read_current_code(self, job_id: str) -> str:
        """Read the current working file for a job."""

        return self.proof_path(job_id).read_text(encoding="utf-8")

    def write_current_code(self, job_id: str, theorem_code: str) -> Path:
        """Overwrite the current working file for a job."""

        path = self.proof_path(job_id)
        path.write_text(theorem_code, encoding="utf-8")
        return path

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
