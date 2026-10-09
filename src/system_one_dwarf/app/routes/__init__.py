"""REST routers — one module per area of ``docs/api.md``."""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    campaigns,
    control,
    decisions,
    health,
    inputs,
    keys,
    memory,
    metrics,
    objective,
    questions,
    screen,
    spend,
)

__all__ = [
    "all_routers",
    "campaigns",
    "control",
    "decisions",
    "health",
    "inputs",
    "keys",
    "memory",
    "metrics",
    "objective",
    "questions",
    "screen",
    "spend",
]


def all_routers() -> list[APIRouter]:
    """Every REST router, in documentation order."""
    return [
        health.router,
        campaigns.router,
        keys.router,
        memory.router,
        objective.router,
        inputs.router,
        metrics.router,
        spend.router,
        decisions.router,
        questions.router,
        control.router,
        screen.router,
    ]
