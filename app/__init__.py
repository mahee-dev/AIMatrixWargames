"""AMAW FastAPI core: game engine, persistence, visibility and scenarios.

This package must never import the agent framework (PydanticAI) or the OpenRouter gateway —
they live in `agents/` and `benchmark/`. See TRD §4 and architecture rule 1 in AGENTS.md §6.
"""
