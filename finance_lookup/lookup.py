from __future__ import annotations

import importlib.util
import json
import math
import os
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from market_lookup.providers.common import ssl_context


CRYPTO_ALIASES = {
    "BTC": "BTC/USDT",
    "BITCOIN": "BTC/USDT",
    "ETH": "ETH/USDT",
    "ETHEREUM": "ETH/USDT",
    "SOL": "SOL/USDT",
    "SOLANA": "SOL/USDT",
    "XRP": "XRP/USDT",
    "DOGE": "DOGE/USDT",
}

MACRO_SERIES = {
    "cpi": "CPIAUCSL",
    "inflation": "CPIAUCSL",
    "unemployment": "UNRATE",
    "jobs": "UNRATE",
    "fed funds": "FEDFUNDS",
    "interest rates": "FEDFUNDS",
    "10y": "DGS10",
    "treasury": "DGS10",
    "gdp": "GDP",
}

DEFAULT_SYMBOL_RE = re.compile(r"\b[A-Z][A-Z0-9.=-]{1,9}\b")


def lookup_finance(
    *,
    query: str = "",
    symbols: str | list[str] | None = None,
    asset_type: str = "auto",
    data_needed: str | list[str] | None = None,
    lookback_days: int = 30,
    max_items: int = 5,
    include_debug: bool = False,
) -> dict[str, Any]:
    clean_query = query.strip()
    symbol_list = parse_symbols(symbols)
    if not symbol_list:
        symbol_list = extract_symbols(clean_query)

    needs = parse_csv(data_needed) or ["price", "history", "news"]
    limit = max(1, min(int(max_items), 25))
    days = max(1, min(int(lookback_days), 365))
    requested_asset_type = asset_type if asset_type in {"auto", "equity", "crypto", "macro", "filings"} else "auto"

    result: dict[str, Any] = {
        "tool": "finance_lookup",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "query": clean_query,
            "symbols": symbol_list,
            "asset_type": requested_asset_type,
            "data_needed": needs,
            "lookback_days": days,
            "max_items": limit,
        },
        "results": [],
        "macro": [],
        "filings": [],
        "errors": [],
    }

    provider_status = build_provider_status()

    for symbol in symbol_list:
        routed_type = route_asset_type(symbol, requested_asset_type)
        if routed_type == "crypto":
            result["results"].append(fetch_crypto(symbol, needs=needs, lookback_days=days, max_items=limit))
        elif routed_type in {"equity", "auto"}:
            result["results"].append(fetch_equity(symbol, needs=needs, lookback_days=days, max_items=limit))

    if requested_asset_type in {"macro", "auto"} or "macro" in needs:
        macro_records = fetch_macro(clean_query, max_items=limit)
        if macro_records:
            result["macro"] = macro_records

    if requested_asset_type == "filings" or "filings" in needs:
        result["filings"] = fetch_sec_filings(symbol_list, max_items=limit)

    if include_debug:
        result["debug"] = {"provider_status": provider_status}
    else:
        result["provider_status"] = [
            {
                "provider": item["provider"],
                "installed": item["installed"],
                "configured": item["configured"],
                "notes": item["notes"],
            }
            for item in provider_status
        ]

    return to_jsonable(result)


def self_test() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(test_import("Yahoo Finance", "yfinance"))
    checks.append(test_import("CCXT", "ccxt"))
    checks.append(test_import("OpenBB", "openbb"))
    checks.append(test_import("FRED", "fredapi"))
    checks.append(test_import("Finnhub", "finnhub"))
    checks.append(test_import("Alpha Vantage", "alpha_vantage"))
    checks.append(test_import("SEC EDGAR", "sec_edgar_downloader"))

    checks.append(test_yahoo_live())
    checks.append(test_crypto_live())
    checks.append(test_openbb_live())
    checks.append(test_fred_live())
    checks.append(test_finnhub_live())
    checks.append(test_alpha_vantage_live())
    checks.append(test_sec_config())

    return {
        "tool": "finance_lookup",
        "test": "provider_self_test",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summarize_checks(checks),
        "checks": checks,
    }


def test_import(provider_name: str, module_name: str) -> dict[str, Any]:
    installed = importlib.util.find_spec(module_name) is not None
    return {
        "provider": provider_name,
        "check": "import",
        "status": "pass" if installed else "fail",
        "detail": f"{module_name} importable" if installed else f"{module_name} is missing",
    }


def test_yahoo_live() -> dict[str, Any]:
    record = fetch_equity("NVDA", needs=["price", "history"], lookback_days=5, max_items=1)
    ok = not record["errors"] and bool(record["quote"] or record["history"])
    return {
        "provider": "Yahoo Finance",
        "check": "live_quote_history",
        "status": "pass" if ok else "fail",
        "detail": "NVDA quote/history returned" if ok else "; ".join(record["errors"]) or "empty response",
    }


def test_crypto_live() -> dict[str, Any]:
    record = fetch_crypto("BTC", needs=["price", "history"], lookback_days=2, max_items=1)
    ok = not record["errors"] and bool(record["quote"] or record["history"])
    return {
        "provider": "CCXT",
        "check": "live_crypto_quote_history",
        "status": "pass" if ok else "fail",
        "detail": f"BTC returned via {record['source']}" if ok else "; ".join(record["errors"]) or "empty response",
    }


def test_openbb_live() -> dict[str, Any]:
    try:
        from openbb import obb  # noqa: F401

        return {
            "provider": "OpenBB",
            "check": "import_runtime",
            "status": "pass",
            "detail": "from openbb import obb succeeded",
        }
    except Exception as exc:
        return {"provider": "OpenBB", "check": "import_runtime", "status": "fail", "detail": str(exc)}


def test_fred_live() -> dict[str, Any]:
    if not os.getenv("FRED_API_KEY"):
        return {
            "provider": "FRED",
            "check": "live_macro_series",
            "status": "skipped",
            "detail": "FRED_API_KEY is not set",
        }
    records = fetch_macro("unemployment", max_items=1)
    ok = bool(records and records[0].get("observations"))
    return {
        "provider": "FRED",
        "check": "live_macro_series",
        "status": "pass" if ok else "fail",
        "detail": "UNRATE returned" if ok else str(records),
    }


def test_finnhub_live() -> dict[str, Any]:
    key = os.getenv("FINNHUB_API_KEY")
    if not key:
        return {
            "provider": "Finnhub",
            "check": "live_quote",
            "status": "skipped",
            "detail": "FINNHUB_API_KEY is not set",
        }
    try:
        import finnhub

        quote = finnhub.Client(api_key=key).quote("AAPL")
        ok = isinstance(quote, dict) and bool(quote)
        return {
            "provider": "Finnhub",
            "check": "live_quote",
            "status": "pass" if ok else "fail",
            "detail": "AAPL quote returned" if ok else "empty response",
        }
    except Exception as exc:
        return {"provider": "Finnhub", "check": "live_quote", "status": "fail", "detail": str(exc)}


def test_alpha_vantage_live() -> dict[str, Any]:
    key = os.getenv("ALPHA_VANTAGE_API_KEY")
    if not key:
        return {
            "provider": "Alpha Vantage",
            "check": "live_quote",
            "status": "skipped",
            "detail": "ALPHA_VANTAGE_API_KEY is not set",
        }
    try:
        from alpha_vantage.timeseries import TimeSeries

        data, _ = TimeSeries(key=key, output_format="json").get_quote_endpoint(symbol="AAPL")
        ok = isinstance(data, dict) and bool(data)
        return {
            "provider": "Alpha Vantage",
            "check": "live_quote",
            "status": "pass" if ok else "fail",
            "detail": "AAPL quote returned" if ok else "empty response",
        }
    except Exception as exc:
        return {"provider": "Alpha Vantage", "check": "live_quote", "status": "fail", "detail": str(exc)}


def test_sec_config() -> dict[str, Any]:
    configured = bool(os.getenv("SEC_USER_AGENT") or (os.getenv("SEC_COMPANY_NAME") and os.getenv("SEC_EMAIL")))
    if not configured:
        return {
            "provider": "SEC EDGAR",
            "check": "identity_config",
            "status": "skipped",
            "detail": "SEC_USER_AGENT or SEC_COMPANY_NAME plus SEC_EMAIL is not set",
        }
    try:
        filings = fetch_sec_filings(["NVDA"], max_items=1)
        ok = bool(filings and filings[0].get("recent_filings"))

        return {
            "provider": "SEC EDGAR",
            "check": "live_recent_filings",
            "status": "pass" if ok else "fail",
            "detail": "NVDA recent filing returned" if ok else str(filings),
        }
    except Exception as exc:
        return {"provider": "SEC EDGAR", "check": "live_recent_filings", "status": "fail", "detail": str(exc)}


def summarize_checks(checks: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"pass": 0, "fail": 0, "skipped": 0}
    for check in checks:
        status = str(check.get("status") or "fail")
        summary[status] = summary.get(status, 0) + 1
    return summary


def fetch_equity(symbol: str, *, needs: list[str], lookback_days: int, max_items: int) -> dict[str, Any]:
    record: dict[str, Any] = {
        "source": "Yahoo Finance",
        "asset_type": "equity",
        "symbol": symbol.upper(),
        "quote": {},
        "history": [],
        "news": [],
        "errors": [],
    }
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol.upper())
        if "price" in needs:
            record["quote"] = normalize_yfinance_quote(ticker)
        if "history" in needs:
            period = f"{max(1, min(lookback_days, 365))}d"
            history = ticker.history(period=period, interval="1d", auto_adjust=False)
            record["history"] = dataframe_tail(history, max_items)
        if "news" in needs:
            record["news"] = normalize_yfinance_news(getattr(ticker, "news", []) or [], max_items)
    except Exception as exc:  # pragma: no cover - live provider errors vary.
        record["errors"].append(f"Yahoo Finance lookup failed: {exc}")
    return record


def fetch_crypto(symbol: str, *, needs: list[str], lookback_days: int, max_items: int) -> dict[str, Any]:
    pair = normalize_crypto_symbol(symbol)
    record: dict[str, Any] = {
        "source": "CCXT",
        "asset_type": "crypto",
        "symbol": pair,
        "quote": {},
        "history": [],
        "errors": [],
    }
    try:
        import ccxt

        errors: list[str] = []
        for exchange_name, exchange, exchange_pair in crypto_exchange_candidates(ccxt, pair):
            try:
                if "price" in needs:
                    ticker = exchange.fetch_ticker(exchange_pair)
                    record["quote"] = {
                        "last": safe_float(ticker.get("last")),
                        "bid": safe_float(ticker.get("bid")),
                        "ask": safe_float(ticker.get("ask")),
                        "base_volume": safe_float(ticker.get("baseVolume")),
                        "quote_volume": safe_float(ticker.get("quoteVolume")),
                        "timestamp": iso_from_millis(ticker.get("timestamp")),
                    }
                if "history" in needs:
                    rows = exchange.fetch_ohlcv(
                        exchange_pair,
                        timeframe="1d",
                        limit=min(max(1, lookback_days), max_items),
                    )
                    record["history"] = [
                        {
                            "date": iso_from_millis(row[0]),
                            "open": safe_float(row[1]),
                            "high": safe_float(row[2]),
                            "low": safe_float(row[3]),
                            "close": safe_float(row[4]),
                            "volume": safe_float(row[5]),
                        }
                        for row in rows[-max_items:]
                    ]
                record["source"] = f"CCXT {exchange_name}"
                record["symbol"] = exchange_pair
                return record
            except Exception as exc:
                errors.append(f"{exchange_name} failed: {exc}")
        record["errors"].extend(errors)
    except Exception as exc:  # pragma: no cover - live provider errors vary.
        record["errors"].append(f"CCXT crypto lookup failed: {exc}")
    return record


def fetch_macro(query: str, *, max_items: int) -> list[dict[str, Any]]:
    if not os.getenv("FRED_API_KEY"):
        return [
            {
                "source": "FRED",
                "configured": False,
                "series_id": None,
                "observations": [],
                "note": "FRED_API_KEY is not set; macro live lookup skipped.",
            }
        ]
    series_ids = infer_macro_series(query)
    if not series_ids:
        return [
            {
                "source": "FRED",
                "configured": True,
                "series_id": None,
                "observations": [],
                "note": "No supported macro series inferred from query.",
            }
        ]
    records: list[dict[str, Any]] = []
    try:
        from fredapi import Fred

        fred = Fred(api_key=os.environ["FRED_API_KEY"])
        for series_id in series_ids[:max_items]:
            series = fred.get_series(series_id).tail(max_items)
            records.append(
                {
                    "source": "FRED",
                    "series_id": series_id,
                    "observations": [
                        {"date": idx.date().isoformat(), "value": safe_float(value)}
                        for idx, value in series.items()
                    ],
                }
            )
    except Exception as exc:  # pragma: no cover - live provider errors vary.
        records.append({"source": "FRED", "series_id": None, "observations": [], "errors": [str(exc)]})
    return records


def filings_status(symbols: list[str]) -> list[dict[str, Any]]:
    configured = bool(os.getenv("SEC_USER_AGENT") or (os.getenv("SEC_COMPANY_NAME") and os.getenv("SEC_EMAIL")))
    return [
        {
            "source": "SEC EDGAR",
            "symbol": symbol.upper(),
            "configured": configured,
            "note": "SEC lookup is installed but not queried unless SEC user-agent/company/email env vars are configured.",
        }
        for symbol in symbols
    ]


def fetch_sec_filings(symbols: list[str], *, max_items: int) -> list[dict[str, Any]]:
    if not symbols:
        return []
    configured = bool(os.getenv("SEC_USER_AGENT") or (os.getenv("SEC_COMPANY_NAME") and os.getenv("SEC_EMAIL")))
    if not configured:
        return filings_status(symbols)
    records: list[dict[str, Any]] = []
    for symbol in symbols:
        try:
            cik = sec_cik_for_symbol(symbol)
            if cik is None:
                records.append(
                    {
                        "source": "SEC EDGAR",
                        "symbol": symbol.upper(),
                        "configured": True,
                        "recent_filings": [],
                        "errors": [f"No SEC CIK found for {symbol.upper()}"],
                    }
                )
                continue
            submissions = sec_get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
            recent = submissions.get("filings", {}).get("recent", {}) if isinstance(submissions, dict) else {}
            filings = []
            forms = recent.get("form") or []
            accession_numbers = recent.get("accessionNumber") or []
            filing_dates = recent.get("filingDate") or []
            report_dates = recent.get("reportDate") or []
            primary_documents = recent.get("primaryDocument") or []
            descriptions = recent.get("primaryDocDescription") or []
            for index, form in enumerate(forms[:max_items]):
                filings.append(
                    {
                        "form": form,
                        "filing_date": item_at(filing_dates, index),
                        "report_date": item_at(report_dates, index),
                        "accession_number": item_at(accession_numbers, index),
                        "primary_document": item_at(primary_documents, index),
                        "description": item_at(descriptions, index),
                    }
                )
            records.append(
                {
                    "source": "SEC EDGAR",
                    "symbol": symbol.upper(),
                    "configured": True,
                    "cik": cik,
                    "company_name": submissions.get("name"),
                    "recent_filings": filings,
                    "errors": [],
                }
            )
        except Exception as exc:
            records.append(
                {
                    "source": "SEC EDGAR",
                    "symbol": symbol.upper(),
                    "configured": True,
                    "recent_filings": [],
                    "errors": [str(exc)],
                }
            )
    return records


def sec_cik_for_symbol(symbol: str) -> str | None:
    payload = sec_get_json("https://www.sec.gov/files/company_tickers.json")
    wanted = symbol.upper()
    if not isinstance(payload, dict):
        return None
    for item in payload.values():
        if not isinstance(item, dict):
            continue
        if str(item.get("ticker") or "").upper() == wanted:
            return str(item.get("cik_str")).zfill(10)
    return None


def sec_get_json(url: str) -> Any:
    user_agent = os.getenv("SEC_USER_AGENT") or f"{os.getenv('SEC_COMPANY_NAME')} {os.getenv('SEC_EMAIL')}"
    request = Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": user_agent,
        },
    )
    try:
        with urlopen(request, timeout=15.0, context=ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc)) from exc


def item_at(values: Any, index: int) -> Any:
    if isinstance(values, list) and index < len(values):
        return values[index]
    return None


def normalize_yfinance_quote(ticker: Any) -> dict[str, Any]:
    quote: dict[str, Any] = {}
    fast_info = getattr(ticker, "fast_info", None)
    if fast_info:
        for key in (
            "last_price",
            "previous_close",
            "open",
            "day_high",
            "day_low",
            "year_high",
            "year_low",
            "market_cap",
            "currency",
        ):
            try:
                quote[key] = to_jsonable(fast_info.get(key))
            except Exception:
                continue
    return quote


def normalize_yfinance_news(news: list[dict[str, Any]], max_items: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in news[:max_items]:
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        normalized.append(
            {
                "title": item.get("title") or content.get("title"),
                "publisher": item.get("publisher") or content.get("provider", {}).get("displayName"),
                "published_at": iso_from_seconds(item.get("providerPublishTime") or content.get("pubDate")),
                "summary": item.get("summary") or content.get("summary"),
            }
        )
    return normalized


def dataframe_tail(frame: Any, max_items: int) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    tail = frame.tail(max_items).reset_index()
    records: list[dict[str, Any]] = []
    for row in tail.to_dict(orient="records"):
        normalized = {}
        for key, value in row.items():
            normalized[snake(str(key))] = to_jsonable(value)
        records.append(normalized)
    return records


def build_provider_status() -> list[dict[str, Any]]:
    return [
        provider("Yahoo Finance", "yfinance", configured=True, notes="quotes, OHLC history, limited news; no key"),
        provider("CCXT", "ccxt", configured=True, notes="crypto exchange quotes/OHLC; no key for public endpoints"),
        provider("OpenBB", "openbb", configured=True, notes="installed as a broad finance toolkit; not used by default CLI path"),
        provider("FRED", "fredapi", configured=bool(os.getenv("FRED_API_KEY")), notes="macro series; set FRED_API_KEY"),
        provider("Finnhub", "finnhub", configured=bool(os.getenv("FINNHUB_API_KEY")), notes="optional news/company data"),
        provider(
            "Alpha Vantage",
            "alpha_vantage",
            configured=bool(os.getenv("ALPHA_VANTAGE_API_KEY")),
            notes="optional equities/forex/crypto data",
        ),
        provider(
            "SEC EDGAR",
            "sec_edgar_downloader",
            configured=bool(os.getenv("SEC_USER_AGENT") or (os.getenv("SEC_COMPANY_NAME") and os.getenv("SEC_EMAIL"))),
            notes="filings package installed; configure SEC identity before live use",
        ),
    ]


def provider(name: str, module: str, *, configured: bool, notes: str) -> dict[str, Any]:
    return {
        "provider": name,
        "module": module,
        "installed": importlib.util.find_spec(module) is not None,
        "configured": configured,
        "notes": notes,
    }


def parse_symbols(symbols: str | list[str] | None) -> list[str]:
    if isinstance(symbols, list):
        raw = symbols
    else:
        raw = parse_csv(symbols)
    cleaned: list[str] = []
    for item in raw:
        symbol = item.strip().upper()
        if symbol and symbol not in cleaned:
            cleaned.append(symbol)
    return cleaned


def parse_csv(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def extract_symbols(query: str) -> list[str]:
    candidates = DEFAULT_SYMBOL_RE.findall(query)
    blocked = {"THE", "AND", "FOR", "WITH", "WILL", "YES", "NO", "GDP", "CPI"}
    symbols: list[str] = []
    for candidate in candidates:
        if candidate in blocked:
            continue
        if candidate not in symbols:
            symbols.append(candidate)
    for token, pair in CRYPTO_ALIASES.items():
        if re.search(rf"\b{re.escape(token)}\b", query, flags=re.IGNORECASE):
            coin = pair.split("/")[0]
            if coin not in symbols:
                symbols.append(coin)
    return symbols[:10]


def route_asset_type(symbol: str, requested: str) -> str:
    if requested != "auto":
        return requested
    if symbol.upper() in CRYPTO_ALIASES or "/" in symbol or symbol.upper().endswith(("USDT", "USD")):
        return "crypto"
    return "equity"


def normalize_crypto_symbol(symbol: str) -> str:
    clean = symbol.strip().upper()
    if clean in CRYPTO_ALIASES:
        return CRYPTO_ALIASES[clean]
    if "/" in clean:
        return clean
    if clean.endswith("USDT") and len(clean) > 4:
        return f"{clean[:-4]}/USDT"
    if clean.endswith("USD") and len(clean) > 3:
        return f"{clean[:-3]}/USDT"
    return f"{clean}/USDT"


def crypto_exchange_candidates(ccxt: Any, pair: str) -> list[tuple[str, Any, str]]:
    base = pair.split("/")[0]
    return [
        ("Coinbase", ccxt.coinbase({"enableRateLimit": True}), f"{base}/USD"),
        ("Kraken", ccxt.kraken({"enableRateLimit": True}), f"{base}/USD"),
        ("BinanceUS", ccxt.binanceus({"enableRateLimit": True}), pair),
        ("Binance", ccxt.binance({"enableRateLimit": True}), pair),
    ]


def infer_macro_series(query: str) -> list[str]:
    lower = query.lower()
    series: list[str] = []
    for term, series_id in MACRO_SERIES.items():
        if term in lower and series_id not in series:
            series.append(series_id)
    return series


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def iso_from_millis(value: Any) -> str | None:
    try:
        if value is None:
            return None
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def iso_from_seconds(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def snake(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return to_jsonable(value.item())
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]
    return str(value)
