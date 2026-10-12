"""The ``amaw`` command-line interface: ``export``, ``run``, ``benchmark``, ``analysis-plan``.

It lives **outside** ``app/`` so the agent stack can never leak into the engine package
(AGENTS.md §6 / TRD §4): ``run`` and ``benchmark`` import ``agents``/``pydantic_ai`` lazily
inside the command, and a plain ``import amaw_cli`` stays framework-free (the import-boundary
test asserts this). ``export`` shares the *one* export builder with
``GET /admin/games/{id}/export`` (:func:`app.export.build_game_export`) and is read-only
against the configured ``AMAW_DATABASE_PATH``. ``run`` and ``benchmark`` are one composition
path (I-061): parse → resolve the game-loop condition and inference profile → compose players,
Umpire and app → drive → write; ``benchmark`` adds rotation, repetition and the batch outputs
(:mod:`benchmark`). The resume recipe has one builder and the CLI one parser.
"""

from __future__ import annotations

# Importing a command module registers its commands on ``app``; the import order is the order
# ``amaw --help`` lists them in.
from amaw_cli._app import app
from amaw_cli.exports import export
from amaw_cli.reporting import publish, report, watch
from amaw_cli.analysis_plan import analysis_plan_app
from amaw_cli.conditions import ConditionFlags, ResolvedCondition
from amaw_cli.run import RESUME_OVERRIDE_FLAGS, run
from amaw_cli.benchmark import benchmark

__all__ = [
    "RESUME_OVERRIDE_FLAGS",
    "ConditionFlags",
    "ResolvedCondition",
    "analysis_plan_app",
    "app",
    "benchmark",
    "export",
    "publish",
    "report",
    "run",
    "watch",
]
