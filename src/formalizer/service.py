"""Thin service facade for the v3 formalizer."""

from __future__ import annotations

from typing import Any

from src.config import FORMALIZER_BACKEND
from src.formalizer.context_builder import FormalizerContextBuilder
from src.formalizer.formalizer import (
    DriverRegistry,
    Formalizer,
    FormalizerBackend,
    FormalizerDriver,
    FormalizerDriverError,
    HuggingFaceFormalizerDriver,
    MistralFormalizerDriver,
)
from src.formalizer.models import FormalizationPacket


class FormalizerService:
    """Backward-compatible entry point for the API layer."""

    def __init__(
        self,
        backend: str = FORMALIZER_BACKEND,
        *,
        mistral_driver: FormalizerDriver | None = None,
        huggingface_driver: FormalizerDriver | None = None,
        context_builder: FormalizerContextBuilder | None = None,
    ) -> None:
        self._formalizer = Formalizer(
            backend=backend,
            mistral_driver=mistral_driver,
            huggingface_driver=huggingface_driver,
            context_builder=context_builder,
        )

    @property
    def backend(self) -> FormalizerBackend:
        return self._formalizer.backend

    @property
    def registry(self) -> DriverRegistry:
        return self._formalizer.registry

    def formalize(
        self,
        claim: str,
        *,
        planner_packet: dict[str, Any] | None = None,
        benchmark_mode: bool = False,
    ) -> FormalizationPacket:
        return self._formalizer.formalize(
            claim,
            planner_packet=planner_packet,
            benchmark_mode=benchmark_mode,
        )


DEFAULT_FORMALIZER = FormalizerService()
