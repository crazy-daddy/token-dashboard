"""Pricing table + plan-aware cost formatting."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Union

from .db import connect


def load_pricing(path: Union[str, Path]) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _tier_from_name(model: str) -> Optional[str]:
    m = (model or "").lower()
    for tier in ("fable", "mythos", "opus", "sonnet", "haiku"):
        if tier in m:
            return tier
    return None


_RATE_KEYS = {
    "input":           "input_tokens",
    "output":          "output_tokens",
    "cache_read":      "cache_read_tokens",
    "cache_create_5m": "cache_create_5m_tokens",
    "cache_create_1h": "cache_create_1h_tokens",
}


def long_context_thresholds(pricing: dict) -> dict:
    """{model: prompt-token limit} for models priced higher above that limit."""
    return {
        model: rates["long_context"]["above"]
        for model, rates in pricing["models"].items()
        if "long_context" in rates
    }


def cost_for(model: str, usage: dict, pricing: dict) -> dict:
    """Return {usd, estimated, breakdown}. usd=None when no tier match.

    When the model has `long_context` rates and `usage` carries `long_<col>`
    sums (see db.model_breakdown), those tokens bill at the long-context
    rates and the rest at base rates.
    """
    rates = pricing["models"].get(model)
    estimated = False
    if rates is None:
        tier = _tier_from_name(model or "")
        if tier and tier in pricing["tier_fallback"]:
            rates = pricing["tier_fallback"][tier]
            estimated = True
        else:
            return {"usd": None, "estimated": True, "breakdown": {}}
    long_rates = rates.get("long_context")
    bd = {}
    for rate_key, col in _RATE_KEYS.items():
        long_tokens = usage.get("long_" + col, 0) if long_rates else 0
        bd[rate_key] = (
            (usage[col] - long_tokens) * rates[rate_key]
            + long_tokens * (long_rates[rate_key] if long_rates else 0)
        ) / 1_000_000
    return {"usd": round(sum(bd.values()), 6), "estimated": estimated, "breakdown": bd}


def get_plan(db_path: Union[str, Path], default: str = "api") -> str:
    with connect(db_path) as c:
        row = c.execute("SELECT v FROM plan WHERE k='plan'").fetchone()
    return row["v"] if row else default


def set_plan(db_path: Union[str, Path], plan: str) -> None:
    with connect(db_path) as c:
        c.execute("INSERT OR REPLACE INTO plan (k, v) VALUES ('plan', ?)", (plan,))
        c.commit()


def format_for_user(api_cost_usd: float, plan: str, pricing: dict) -> dict:
    p = pricing["plans"].get(plan, pricing["plans"]["api"])
    if plan == "api" or p["monthly"] == 0:
        return {"display_usd": api_cost_usd, "subtitle": None, "subscription_usd": None}
    return {
        "display_usd":      api_cost_usd,
        "subtitle":         f"You pay ${p['monthly']}/mo on {p['label']}",
        "subscription_usd": p["monthly"],
    }
