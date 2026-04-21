# Econ Preamble Model

## Role

The Preamble is a machine-usable library of economic primitives, reusable lemmas, and short source excerpts. Its job is to ground planner and formalizer decisions in stable concepts, not to mirror textbooks chapter by chapter.

## Design Principles

- Put cross-cutting concepts in `Foundations/`.
- Keep entries granular so retrieval can be precise.
- Use metadata to expose concepts, related ideas, and key lemmas.
- Optimize for reusability and kernel-checked truth, not coverage theater.

## Retrieval Guidance

- Prefer entries whose definitions and proven lemmas directly match the claim shape.
- Use related concepts to broaden context only when the primary entry is insufficient.
- Treat tactic hints as weak guidance; the theorem statement matters more.

## Anti-Patterns

- Do not depend on folder names as semantic truth.
- Do not duplicate the same concept across domains.
- Do not store long tactic playbooks or benchmark-specific advice in metadata.
