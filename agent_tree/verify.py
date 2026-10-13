"""Read-only cross-checks against T3 Code and Vorssaint rate data."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .pricing import load_prices


def _result(check_id: str, label: str, status: str, detail: str, **counts: int) -> dict:
    out = {"id": check_id, "label": label, "status": status, "detail": detail}
    out.update(counts)
    return out


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _usage_totals(conn, raw_file: str, source: str | None = None) -> tuple[int, int, int, int]:
    sql = "SELECT COALESCE(SUM(input),0), COALESCE(SUM(cached_input),0), COALESCE(SUM(cache_write),0), COALESCE(SUM(output),0) FROM usage WHERE raw_file=?"
    args: list[Any] = [raw_file]
    if source:
        sql += " AND source=?"
        args.append(source)
    row = conn.execute(sql, args).fetchone()
    return tuple(int(x or 0) for x in row)


def _decode_t3_row(row: list[Any], models: list[str]) -> tuple[str | None, tuple[int, int, int, int]] | None:
    """Decode T3 usage cache rows.

    Current T3 rows are [timestamp_ms, model_index, session_index, input,
    cached_input, cache_write, output, reasoning, request_id, ...].
    """
    if not isinstance(row, list) or len(row) < 8:
        return None
    try:
        model_idx = int(row[1])
        model = models[model_idx] if 0 <= model_idx < len(models) else None
        values = tuple(max(0, int(row[i] or 0)) for i in (3, 4, 5, 6))
    except (TypeError, ValueError, IndexError):
        return None
    return model, values


def _check_t3_usage(conn, cfg) -> dict:
    path = Path(cfg.t3_dir) / "userdata" / "usage-scan-cache.json"
    if not path.is_file():
        return _result("t3-usage", "Token totals vs T3 Code", "unavailable", "T3 Code isn't installed")
    try:
        data = _read_json(path)
        files = data.get("files") or {}
        models = data.get("models") or []
        compared = matched = differing = skipped = 0
        largest = 0.0
        for raw_file, info in files.items():
            if not isinstance(info, dict) or not isinstance(info.get("r"), list):
                continue
            try:
                size, offset = int(info.get("s", -1)), int(info.get("o", -1))
            except (TypeError, ValueError):
                continue
            try:
                actual_size = Path(raw_file).stat().st_size
            except OSError:
                skipped += 1
                continue
            if size < 0 or offset != actual_size:
                skipped += 1
                continue
            source_tag = str(info.get("p") or "").lower()
            source = "codex" if source_tag == "codex" else "claude-code" if source_tag in ("claude", "claude-code") else None
            decoded = [_decode_t3_row(r, models) for r in info["r"]]
            decoded = [x for x in decoded if x is not None]
            if not decoded:
                continue
            if source is None:
                skipped += 1
                continue
            t3_totals = tuple(sum(x[1][i] for x in decoded) for i in range(4))
            ours = _usage_totals(conn, str(raw_file), source)
            compared += 1
            if ours == t3_totals:
                matched += 1
            else:
                differing += 1
                denom = max(1, sum(t3_totals))
                largest = max(largest, abs(sum(ours) - sum(t3_totals)) / denom * 100)
        if compared == 0:
            detail = f"No fully scanned transcripts to compare; skipped {skipped}"
            return _result("t3-usage", "Token totals vs T3 Code", "unavailable", detail, compared=0, matched=0)
        status = "match" if matched == compared else "mismatch"
        detail = f"{matched} of {compared} transcripts agree"
        if differing:
            detail += f"; {differing} differ (largest gap {largest:.1f}%)"
        if skipped:
            detail += f"; {skipped} incomplete or unsupported files skipped"
        return _result("t3-usage", "Token totals vs T3 Code", status, detail, compared=compared, matched=matched)
    except Exception as exc:
        return _result("t3-usage", "Token totals vs T3 Code", "unavailable", f"{type(exc).__name__}: {exc}")


def _norm_model(value: str) -> str:
    return str(value).strip().lower().split("/")[-1]


def _find_rate(table: dict, model: str) -> dict | None:
    key = _norm_model(model)
    for name, row in table.items():
        if _norm_model(name) == key and isinstance(row, dict):
            return row
    return None


def _compare_rates(conn, cfg, source_name: str, label: str, table: dict, missing_detail: str) -> dict:
    try:
        prices = load_prices(cfg)
        models = [str(r[0]) for r in conn.execute("SELECT DISTINCT model FROM usage WHERE model IS NOT NULL ORDER BY model")]
        compared = matched = 0
        differences: list[str] = []
        for model in models:
            ours = prices.lookup(model)
            theirs = _find_rate(table, model)
            if ours is None or theirs is None:
                differences.append(model)
                continue
            vals = {
                "input": (theirs.get("input_cost_per_token"), True) if "input_cost_per_token" in theirs else (theirs.get("input"), False),
                "cached_input": (theirs.get("cache_read_input_token_cost"), True) if "cache_read_input_token_cost" in theirs else (theirs.get("cacheRead", theirs.get("cached_input")), False),
                "cache_write": (theirs.get("cache_creation_input_token_cost"), True) if "cache_creation_input_token_cost" in theirs else (theirs.get("cacheWrite", theirs.get("cache_write")), False),
                "output": (theirs.get("output_cost_per_token"), True) if "output_cost_per_token" in theirs else (theirs.get("output"), False),
            }
            pairs = []
            for field, (value, per_token) in vals.items():
                if value is not None:
                    pairs.append((getattr(ours, field), float(value) * (1_000_000 if per_token else 1)))
            if not pairs:
                differences.append(model)
                continue
            compared += 1
            ok = all(abs(a - b) <= max(abs(a), abs(b), 1e-9) * 0.01 for a, b in pairs)
            if ok:
                matched += 1
            else:
                differences.append(model)
        if not models:
            return _result(source_name, label, "unavailable", "No models in usage table", compared=0, matched=0)
        status = "match" if matched == len(models) else "mismatch"
        detail = f"{matched} of {len(models)} models agree"
        if differences:
            detail += "; differing or missing: " + ", ".join(differences[:8])
        return _result(source_name, label, status, detail, compared=compared, matched=matched)
    except Exception as exc:
        return _result(source_name, label, "unavailable", f"{type(exc).__name__}: {exc}")


def _check_t3_prices(conn, cfg) -> dict:
    path = Path(cfg.t3_dir) / "userdata" / "usage-model-rates.json"
    if not path.is_file():
        return _result("t3-prices", "Prices vs T3 Code's rate table", "unavailable", "T3 Code isn't installed")
    try:
        data = _read_json(path)
        return _compare_rates(conn, cfg, "t3-prices", "Prices vs T3 Code's rate table", data.get("document") or {}, "T3 Code isn't installed")
    except Exception as exc:
        return _result("t3-prices", "Prices vs T3 Code's rate table", "unavailable", f"{type(exc).__name__}: {exc}")


def _check_vorssaint_prices(conn, cfg) -> dict:
    base = Path(os.environ.get("AGENT_TREE_VORSSAINT_DIR", str(Path.home() / "Library/Application Support/com.vorssaint.utils")))
    path = base / "agent-prices.json"
    if not path.is_file():
        return _result("vorssaint-prices", "Prices vs Vorssaint", "unavailable", "Vorssaint isn't installed")
    try:
        data = _read_json(path)
        table: dict[str, dict] = {}
        for provider in ("claude", "codex"):
            for row in (data.get(provider) or {}).get("models", []):
                if isinstance(row, dict) and row.get("id"):
                    table[str(row["id"])] = row
        return _compare_rates(conn, cfg, "vorssaint-prices", "Prices vs Vorssaint", table, "Vorssaint isn't installed")
    except Exception as exc:
        return _result("vorssaint-prices", "Prices vs Vorssaint", "unavailable", f"{type(exc).__name__}: {exc}")


def run_checks(conn, cfg) -> dict:
    return {"checks": [_check_t3_usage(conn, cfg), _check_t3_prices(conn, cfg), _check_vorssaint_prices(conn, cfg)]}
