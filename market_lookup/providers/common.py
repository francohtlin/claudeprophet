from __future__ import annotations

import json
import math
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ProviderResult:
    markets: list[dict[str, Any]]
    debug: dict[str, Any]


def get_json(url: str, params: dict[str, Any] | None = None, *, timeout: float = 10.0) -> Any:
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "claude-prophet/0.1",
        },
    )
    try:
        context = ssl_context()
        with urlopen(request, timeout=timeout, context=context) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc)) from exc


def ssl_context() -> ssl.SSLContext | None:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def parse_jsonish_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def probability(value: Any) -> float | None:
    parsed = number(value)
    if parsed is None:
        return None
    if 0 <= parsed <= 1:
        return parsed
    if 0 <= parsed <= 100:
        return parsed / 100
    return None


def complement(value: float | None) -> float | None:
    if value is None:
        return None
    return round(max(0.0, min(1.0, 1.0 - value)), 6)


def spread(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return round(max(0.0, ask - bid), 6)


def midpoint(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return round((bid + ask) / 2, 6)


def compact_text(value: Any, *, max_chars: int = 5000) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def first_paragraph(value: Any, *, max_chars: int = 900) -> str | None:
    text = compact_text(value, max_chars=max_chars * 4)
    if text is None:
        return None
    paragraph = text.split("\n\n", 1)[0].strip()
    return compact_text(paragraph, max_chars=max_chars)
