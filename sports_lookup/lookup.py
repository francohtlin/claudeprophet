from __future__ import annotations

import os
import re
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from typing import Any

from market_lookup.providers.common import get_json, number


ESPN_SPORTS = {
    "nfl": ("football", "nfl"),
    "nba": ("basketball", "nba"),
    "mlb": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "soccer": ("soccer", "eng.1"),
    "tennis": ("tennis", "atp"),
}

ODDS_API_SPORTS = {
    "nfl": "americanfootball_nfl",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl",
    "soccer": "soccer_epl",
    "tennis": "tennis_atp",
}

TOKEN_RE = re.compile(r"[a-z0-9]+")


def lookup_sports(
    *,
    query: str = "",
    sport: str = "auto",
    date: str | None = None,
    days: int = 7,
    max_events: int = 10,
    include_odds: bool = False,
    include_debug: bool = False,
) -> dict[str, Any]:
    clean_query = query.strip()
    sports = infer_sports(clean_query, sport)
    start_date = parse_start_date(date)
    duration = max(1, min(int(days), 30))
    limit = max(1, min(int(max_events), 50))
    debug: dict[str, Any] = {"queries": [], "errors": []}

    events: list[dict[str, Any]] = []
    for sport_key in sports:
        events.extend(fetch_espn_events(sport_key, clean_query, start_date, duration, debug))
    events = filter_events(events, clean_query)
    events = sort_events(events)[:limit]

    odds: list[dict[str, Any]] = []
    if include_odds:
        odds = fetch_bookmaker_odds(sports, clean_query, debug, max_events=limit)

    result: dict[str, Any] = {
        "tool": "sports_lookup",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "query": clean_query,
            "sport": sport,
            "resolved_sports": sports,
            "date": start_date.isoformat(),
            "days": duration,
            "max_events": limit,
            "include_odds": include_odds,
        },
        "events": events,
        "bookmaker_odds": odds,
        "provider_status": provider_status(),
    }
    if include_debug:
        result["debug"] = debug
    return result


def fetch_espn_events(
    sport_key: str,
    query: str,
    start_date: date_type,
    days: int,
    debug: dict[str, Any],
) -> list[dict[str, Any]]:
    if sport_key not in ESPN_SPORTS:
        return []
    sport_path, league = ESPN_SPORTS[sport_key]
    records: list[dict[str, Any]] = []
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        params = {"dates": day.strftime("%Y%m%d"), "limit": 100}
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league}/scoreboard"
        debug["queries"].append({"provider": "ESPN", "sport": sport_key, "url": url, **params})
        try:
            payload = get_json(url, params, timeout=10.0)
        except Exception as exc:
            debug["errors"].append({"provider": "ESPN", "sport": sport_key, "date": day.isoformat(), "error": str(exc)})
            continue
        for event in payload.get("events") or []:
            if isinstance(event, dict):
                records.append(normalize_espn_event(event, sport_key))
    return records


def normalize_espn_event(event: dict[str, Any], sport_key: str) -> dict[str, Any]:
    competition = first_dict(event.get("competitions"))
    competitors = []
    for competitor in competition.get("competitors") or []:
        team = competitor.get("team") or {}
        records = competitor.get("records") or []
        competitors.append(
            {
                "team": team.get("displayName") or team.get("name"),
                "abbreviation": team.get("abbreviation"),
                "home_away": competitor.get("homeAway"),
                "score": number(competitor.get("score")),
                "winner": competitor.get("winner"),
                "records": [
                    {"name": item.get("name"), "summary": item.get("summary")}
                    for item in records
                    if isinstance(item, dict)
                ],
            }
        )
    odds = first_dict(competition.get("odds"))
    return {
        "source": "ESPN",
        "sport": sport_key,
        "event": event.get("name") or event.get("shortName"),
        "short_name": event.get("shortName"),
        "start_time": event.get("date"),
        "status": (event.get("status") or {}).get("type", {}).get("description"),
        "completed": (event.get("status") or {}).get("type", {}).get("completed"),
        "venue": (competition.get("venue") or {}).get("fullName"),
        "competitors": competitors,
        "broadcasts": [
            item.get("names") or item.get("name")
            for item in competition.get("broadcasts") or []
            if isinstance(item, dict)
        ],
        "espn_odds": normalize_espn_odds(odds),
    }


def normalize_espn_odds(odds: dict[str, Any]) -> dict[str, Any]:
    if not odds:
        return {}
    return {
        "provider": odds.get("provider", {}).get("name") if isinstance(odds.get("provider"), dict) else odds.get("provider"),
        "details": odds.get("details"),
        "over_under": number(odds.get("overUnder")),
        "spread": number(odds.get("spread")),
        "home_team_odds": odds.get("homeTeamOdds"),
        "away_team_odds": odds.get("awayTeamOdds"),
    }


def fetch_bookmaker_odds(
    sports: list[str],
    query: str,
    debug: dict[str, Any],
    *,
    max_events: int,
) -> list[dict[str, Any]]:
    api_keys = odds_api_keys()
    if not api_keys:
        return [
            {
                "source": "The Odds API",
                "configured": False,
                "note": "THE_ODDS_API_KEY is not set; bookmaker odds lookup skipped.",
            }
        ]
    records: list[dict[str, Any]] = []
    for sport in sports:
        odds_sport = ODDS_API_SPORTS.get(sport)
        if not odds_sport:
            continue
        url = f"https://api.the-odds-api.com/v4/sports/{odds_sport}/odds"
        markets = "h2h,spreads,totals"
        debug["queries"].append({"provider": "The Odds API", "sport": sport, "url": url, "markets": markets})
        payload = None
        for key_index, api_key in enumerate(api_keys, start=1):
            params = {
                "apiKey": api_key,
                "regions": "us",
                "markets": markets,
                "oddsFormat": "american",
                "dateFormat": "iso",
            }
            try:
                payload = get_json(url, params, timeout=15.0)
                break
            except Exception as exc:
                debug["errors"].append(
                    {"provider": "The Odds API", "sport": sport, "key_index": key_index, "error": str(exc)}
                )
                continue
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    normalized = normalize_odds_api_event(item, sport)
                    if event_matches_query(normalized, query):
                        records.append(normalized)
    return records[:max_events]


def normalize_odds_api_event(item: dict[str, Any], sport: str) -> dict[str, Any]:
    return {
        "source": "The Odds API",
        "sport": sport,
        "event": f"{item.get('away_team')} @ {item.get('home_team')}",
        "start_time": item.get("commence_time"),
        "home_team": item.get("home_team"),
        "away_team": item.get("away_team"),
        "bookmakers": [
            {
                "bookmaker": book.get("title"),
                "last_update": book.get("last_update"),
                "markets": [
                    {
                        "key": market.get("key"),
                        "last_update": market.get("last_update"),
                        "outcomes": market.get("outcomes"),
                    }
                    for market in book.get("markets") or []
                    if isinstance(market, dict)
                ],
            }
            for book in item.get("bookmakers") or []
            if isinstance(book, dict)
        ],
    }


def infer_sports(query: str, sport: str) -> list[str]:
    requested = sport.strip().lower()
    if requested in ESPN_SPORTS:
        return [requested]
    lower = query.lower()
    inferred: list[str] = []
    hints = {
        "nfl": ("nfl", "football", "super bowl", "quarterback"),
        "nba": ("nba", "basketball", "lakers", "celtics", "warriors", "knicks", "thunder"),
        "mlb": ("mlb", "baseball", "world series", "dodgers", "yankees", "mets"),
        "nhl": ("nhl", "hockey", "stanley cup", "rangers", "bruins"),
        "soccer": ("soccer", "premier league", "champions league", "epl", "arsenal", "chelsea"),
        "tennis": ("tennis", "atp", "wta", "wimbledon", "french open", "us open"),
    }
    for key, terms in hints.items():
        if any(term in lower for term in terms):
            inferred.append(key)
    return inferred or ["nfl", "nba", "mlb", "nhl"]


def filter_events(events: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    if not query.strip():
        return events
    return [event for event in events if event_matches_query(event, query)]


def event_matches_query(event: dict[str, Any], query: str) -> bool:
    terms = meaningful_terms(query)
    if not terms:
        return True
    haystack = event_text(event)
    tokens = set(TOKEN_RE.findall(haystack.lower()))
    return any(term in tokens for term in terms)


def event_text(event: dict[str, Any]) -> str:
    parts = [str(event.get("event") or ""), str(event.get("short_name") or "")]
    for competitor in event.get("competitors") or []:
        if isinstance(competitor, dict):
            parts.append(str(competitor.get("team") or ""))
            parts.append(str(competitor.get("abbreviation") or ""))
    parts.append(str(event.get("home_team") or ""))
    parts.append(str(event.get("away_team") or ""))
    return " ".join(parts)


def meaningful_terms(query: str) -> list[str]:
    stop = {
        "will",
        "the",
        "and",
        "or",
        "a",
        "an",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "for",
        "vs",
        "nba",
        "nfl",
        "mlb",
        "nhl",
        "game",
        "match",
    }
    return [term for term in TOKEN_RE.findall(query.lower()) if len(term) >= 3 and term not in stop]


def sort_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(events, key=lambda item: str(item.get("start_time") or ""))


def parse_start_date(value: str | None) -> date_type:
    if not value:
        return datetime.now(timezone.utc).date()
    return datetime.fromisoformat(value).date()


def provider_status() -> list[dict[str, Any]]:
    return [
        {"provider": "ESPN", "configured": True, "notes": "public scoreboard/schedule endpoints; no key"},
        {
            "provider": "The Odds API",
            "configured": bool(odds_api_keys()),
            "notes": "optional bookmaker odds; set THE_ODDS_API_KEY; THE_ODDS_API_KEY_2 is used as fallback",
        },
    ]


def self_test() -> dict[str, Any]:
    sample = lookup_sports(query="NBA", sport="nba", days=3, max_events=2, include_odds=True)
    events_ok = isinstance(sample.get("events"), list)
    odds_status = sample.get("bookmaker_odds", [])
    return {
        "tool": "sports_lookup",
        "test": "provider_self_test",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": [
            {
                "provider": "ESPN",
                "check": "scoreboard_query",
                "status": "pass" if events_ok else "fail",
                "detail": f"returned {len(sample.get('events') or [])} normalized events",
            },
            {
                "provider": "The Odds API",
                "check": "bookmaker_odds",
                "status": "pass" if odds_api_keys() and odds_status else "skipped",
                "detail": "odds API key configured" if odds_api_keys() else "THE_ODDS_API_KEY is not set",
            },
        ],
    }


def odds_api_keys() -> list[str]:
    keys: list[str] = []
    for name in ("THE_ODDS_API_KEY", "THE_ODDS_API_KEY_2"):
        value = os.getenv(name)
        if value and value not in keys:
            keys.append(value)
    return keys


def first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    return value if isinstance(value, dict) else {}
