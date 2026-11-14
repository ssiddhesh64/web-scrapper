"""Turn BrowserConfig into a form description, and form values back into a BrowserConfig.

The UI form is generated from the dataclass, so adding a field to `config.py` makes it show up
in the Lab automatically. For a tooltip, either add an entry to HELP below or declare the field
with `field(default=..., metadata={"help": "..."})`.
"""

import types
from dataclasses import MISSING, asdict, fields, is_dataclass
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from ..config import BrowserConfig

HELP = {
    "os": "OS the fingerprint claims. Matching your real OS is the most consistent choice.",
    "headless": "false = visible window (hardest to detect). 'virtual' = Xvfb, Linux only.",
    "humanize": "Human-like cursor movement. true, false, or max seconds per move (e.g. 2.0).",
    "max_screen": "Upper bound for the generated screen size (width, height).",
    "proxy": "Leave server empty to use SCRAPER_PROXY_* env vars (or no proxy).",
    "geoip": "auto = on when a proxy is set. Aligns timezone/locale/WebRTC with the exit IP.",
    "block_images": "Skip image downloads. Saves bandwidth/proxy traffic.",
    "profile_dir": "e.g. profiles/reddit1 — keeps cookies between runs. Empty = fresh browser.",
    "extra": 'Raw Camoufox kwargs as JSON, e.g. {"disable_coop": true}.',
}


def _is_union(tp) -> bool:
    return get_origin(tp) in (Union, types.UnionType)


def _spec(tp) -> dict[str, Any]:
    if tp is bool:
        return {"kind": "bool"}
    origin, args = get_origin(tp), get_args(tp)
    if origin is Literal:
        return {"kind": "choice", "options": list(args)}
    if _is_union(tp):
        has_none = type(None) in args
        non_none = [a for a in args if a is not type(None)]
        literals = [v for a in non_none if get_origin(a) is Literal for v in get_args(a)]
        plain = [a for a in non_none if get_origin(a) is not Literal]
        if plain == [bool]:
            options = [False, True, *literals] if literals else [None, True, False]
            if has_none and None not in options:
                options.insert(0, None)
            return {"kind": "choice", "options": options}
        if set(plain) in ({bool, float}, {bool, int}):
            return {"kind": "boolnum"}
        if len(plain) == 1 and is_dataclass(plain[0]):
            sub = [
                {"name": f.name, "secret": "password" in f.name or "token" in f.name}
                for f in fields(plain[0])
            ]
            return {"kind": "group", "fields": sub}
        if len(plain) == 1:
            return {**_spec(plain[0]), "nullable": has_none}
    if origin is tuple:
        return {"kind": "ints", "length": len(args)}
    if tp in (int, float):
        return {"kind": "number"}
    if tp in (str, Path):
        return {"kind": "text"}
    return {"kind": "json"}


def _jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    if is_dataclass(value):
        return asdict(value)
    return value


def config_schema() -> list[dict[str, Any]]:
    hints = get_type_hints(BrowserConfig)
    out = []
    for f in fields(BrowserConfig):
        if f.default is not MISSING:
            default = f.default
        elif f.default_factory is not MISSING:
            default = f.default_factory()
        else:
            default = None
        out.append(
            {
                **_spec(hints[f.name]),
                "name": f.name,
                "default": _jsonable(default),
                "help": f.metadata.get("help") or HELP.get(f.name, ""),
            }
        )
    return out


def _coerce(tp, value):
    if value is None:
        return None
    if _is_union(tp):
        args = get_args(tp)
        if value == "" and type(None) in args:
            return None
        for a in args:
            if is_dataclass(a) and isinstance(value, dict):
                names = [f.name for f in fields(a)]
                cleaned = {k: (value.get(k) or None) for k in names}
                return a(**cleaned) if cleaned[names[0]] else None
            if a is Path and isinstance(value, str):
                return Path(value)
        return value
    if get_origin(tp) is tuple:
        return tuple(value)
    if tp is Path:
        return Path(value)
    return value


def config_from_dict(data: dict[str, Any]) -> BrowserConfig:
    """Form values -> BrowserConfig. An empty proxy falls back to SCRAPER_PROXY_* env vars."""
    hints = get_type_hints(BrowserConfig)
    values = {}
    for f in fields(BrowserConfig):
        if f.name in data:
            values[f.name] = _coerce(hints[f.name], data[f.name])
    if values.get("proxy", "unset") is None:
        values.pop("proxy")
    return BrowserConfig.from_env(**values)
