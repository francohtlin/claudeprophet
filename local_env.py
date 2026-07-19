from __future__ import annotations

import os
from pathlib import Path


def load_local_env(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = strip_quotes(value.strip())
        if key and key not in os.environ:
            os.environ[key] = value
    configure_certifi()


def strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def configure_certifi() -> None:
    try:
        import certifi  # type: ignore
    except Exception:
        return
    bundle = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)
