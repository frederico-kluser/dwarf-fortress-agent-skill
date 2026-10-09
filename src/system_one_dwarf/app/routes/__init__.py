"""REST routers — one module per area of ``docs/api.md``."""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    adventures,
    approvals,
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
    system,
)

__all__ = [
    "all_routers",
    "adventures",
    "approvals",
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
    "system",
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
        approvals.router,
        control.router,
        screen.router,
        adventures.router,
        system.router,
    ]
