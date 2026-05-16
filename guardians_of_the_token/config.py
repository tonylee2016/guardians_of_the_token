from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Optional


DEFAULT_CONFIG = {
    "warn_threshold_pct": 20,
    "max_output_tokens": 8_000,
    "default_input_price_per_million": 3.0,
    "telemetry_enabled": False,
    "telemetry_host": "https://us.i.posthog.com",
    "telemetry_api_key": "phc_nh4ZxPkc7Y2QHZqRzu88NWuPKJNcHJwSf7SZuiKdgUpf",
    "whitelist": [],
    "ignore": [],
    "prices": {},
    "prompt_guard": {
        "enabled": True,
        "context_window_tokens": 200_000,
        "block_context_pct": 0.30,
        "very_low_similarity": None,
        "unblock_prefix": "GOT_UNBLOCK",
        "embedding_backend": "onnx",
        "embedding_model": "all-MiniLM-L6-v2",
        "fallback_user_prompts": 3,
    },
}

USER_CONFIG = Path("~/.guardians.json").expanduser()
PROJECT_CONFIG_NAMES = (".guardians.toml", ".guardians.json")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _parse_array(raw: str) -> list[Any]:
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    parts = []
    current = []
    quote = ""
    for ch in inner:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in {"'", '"'}:
            quote = ch
            current.append(ch)
            continue
        if ch == ",":
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return [_parse_scalar(part) for part in parts if part]


def _load_toml_subset(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    section: Optional[str] = None
    try:
        lines = path.read_text().splitlines()
    except Exception:
        return {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            if section:
                result.setdefault(section, {})
            continue
        if "=" not in stripped:
            continue
        key, raw_value = [part.strip() for part in stripped.split("=", 1)]
        if "#" in raw_value:
            raw_value = raw_value.split("#", 1)[0].strip()
        value = _parse_array(raw_value) if raw_value.startswith("[") else _parse_scalar(raw_value)
        if section:
            result.setdefault(section, {})[key] = value
        else:
            result[key] = value
    return result


def load_file_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.suffix == ".json":
        return _load_json(path)
    if path.suffix == ".toml":
        return _load_toml_subset(path)
    return {}


def merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def find_project_config(start: Optional[str | Path]) -> tuple[Optional[Path], Optional[Path]]:
    if start is None:
        current = Path.cwd()
    else:
        candidate = Path(start).expanduser().resolve()
        current = candidate.parent if candidate.is_file() else candidate
    for directory in (current, *current.parents):
        for name in PROJECT_CONFIG_NAMES:
            path = directory / name
            if path.exists():
                return path, directory
        if (directory / ".git").exists():
            break
    return None, None


def load_config(start: Optional[str | Path] = None) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    config = merge_config(config, load_file_config(USER_CONFIG))
    env_config = os.environ.get("GUARDIANS_CONFIG")
    if env_config:
        config = merge_config(config, load_file_config(Path(env_config).expanduser()))
    project_config, project_root = find_project_config(start)
    if project_config:
        config = merge_config(config, load_file_config(project_config))
        config["project_config_path"] = str(project_config)
        config["project_root"] = str(project_root)
    return config


def input_price_for(config: dict[str, Any], model: str = "") -> float:
    prices = config.get("prices", {})
    if isinstance(prices, dict) and model:
        for prefix, value in prices.items():
            if model.startswith(str(prefix)):
                try:
                    if isinstance(value, dict):
                        return float(value.get("input_per_million", config["default_input_price_per_million"]))
                    return float(value)
                except Exception:
                    break
    try:
        return float(config.get("default_input_price_per_million", 3.0))
    except Exception:
        return 3.0


def estimate_cost(tokens: int, config: dict[str, Any], model: str = "") -> float:
    return round((tokens / 1_000_000) * input_price_for(config, model), 4)


def _path_candidates(path: str, project_root: Optional[str]) -> list[str]:
    expanded = str(Path(path).expanduser())
    candidates = [expanded]
    try:
        resolved = str(Path(path).expanduser().resolve())
        candidates.append(resolved)
        if project_root:
            candidates.append(str(Path(resolved).relative_to(Path(project_root).resolve())))
    except Exception:
        pass
    return candidates


def matches_any_path(path: str, patterns: list[str], project_root: Optional[str] = None) -> bool:
    candidates = _path_candidates(path, project_root)
    for pattern in patterns or []:
        text = str(pattern)
        for candidate in candidates:
            if fnmatch.fnmatch(candidate, text) or fnmatch.fnmatch(Path(candidate).name, text):
                return True
    return False


def policy_decision(path: str, config: dict[str, Any]) -> str:
    project_root = config.get("project_root")
    if matches_any_path(path, config.get("whitelist", []), project_root):
        return "whitelisted"
    if matches_any_path(path, config.get("ignore", []), project_root):
        return "ignored"
    return "guarded"
