"""Cost estimates from the user-editable prices.toml."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from .config import Config, load_toml

_DATE_SUFFIX = re.compile(r"(-\d{8}|-\d{4}-\d{2}-\d{2}|@\d{8})$")


@dataclass(frozen=True)
class Price:
    model: str
    input: float
    cached_input: float
    cache_write: float
    output: float
    long_threshold: int | None = None
    long_input: float | None = None
    long_cached_input: float | None = None
    long_cache_write: float | None = None
    long_output: float | None = None
    as_of: str | None = None
    source: str | None = None
    note: str | None = None


class PriceBook:
    def __init__(self, raw: dict):
        self.raw = raw
        self.prices: dict[str, Price] = {}
        self.aliases: dict[str, str] = {}
        for name, row in (raw.get("models") or {}).items():
            try:
                price = Price(
                    model=name,
                    input=float(row["input"]),
                    cached_input=float(row.get("cached_input", row["input"])),
                    cache_write=float(row.get("cache_write", row["input"])),
                    output=float(row["output"]),
                    long_threshold=int(row["long_context_threshold"]) if "long_context_threshold" in row else None,
                    long_input=_opt(row.get("long_input")),
                    long_cached_input=_opt(row.get("long_cached_input")),
                    long_cache_write=_opt(row.get("long_cache_write")),
                    long_output=_opt(row.get("long_output")),
                    as_of=row.get("as_of"),
                    source=row.get("source"),
                    note=row.get("note"),
                )
            except (KeyError, TypeError, ValueError):
                continue  # a broken row means "unknown model", never a crash
            self.prices[name.lower()] = price
            for alias in row.get("aliases", []) or []:
                self.aliases[str(alias).lower()] = name.lower()
        self.fingerprint = hashlib.sha1(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:12]

    def lookup(self, model: str | None) -> Price | None:
        if not model:
            return None
        key = model.lower().strip()
        for candidate in (key, _DATE_SUFFIX.sub("", key)):
            if candidate in self.prices:
                return self.prices[candidate]
            if candidate in self.aliases:
                return self.prices[self.aliases[candidate]]
        # Provider prefixes such as "anthropic/claude-…" or "openai/gpt-…".
        if "/" in key:
            return self.lookup(key.rsplit("/", 1)[1])
        return None

    def cost(self, model: str | None, input: int, cached_input: int, cache_write: int, output: int) -> float | None:
        """Dollar estimate for one usage row, or None when the model has no price."""
        price = self.lookup(model)
        if price is None:
            return None
        prompt = input + cached_input + cache_write
        long = price.long_threshold is not None and prompt > price.long_threshold
        rate_in = price.long_input if long and price.long_input is not None else price.input
        rate_cached = price.long_cached_input if long and price.long_cached_input is not None else price.cached_input
        rate_write = price.long_cache_write if long and price.long_cache_write is not None else price.cache_write
        rate_out = price.long_output if long and price.long_output is not None else price.output
        return (input * rate_in + cached_input * rate_cached + cache_write * rate_write + output * rate_out) / 1_000_000


def _opt(value) -> float | None:
    return None if value is None else float(value)


def load_prices(cfg: Config) -> PriceBook:
    return PriceBook(load_toml(cfg.home, "prices.toml"))
