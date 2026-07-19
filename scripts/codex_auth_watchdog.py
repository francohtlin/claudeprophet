#!/usr/bin/env python3
"""Watch CodexProphet Codex auth and report whether it is ChatGPT-backed."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
LOG_DIR = ROOT / "logs" / "auth-watchdog"
AUTH_ERROR_MARKERS = (
    "401 Unauthorized",
    "refresh_token",
    "invalid_request_error",
    "Not logged in",
    "Missing bearer",
    "access token could not be refreshed",
)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def codex_env(config: dict[str, str], *, use_default_home: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("OPENAI_API_KEY", "CODEX_ACCESS_TOKEN", "CODEX_FORCE_API_KEY_AUTH"):
        env.pop(key, None)
    if use_default_home:
        env.pop("CODEX_HOME", None)
    elif config.get("CODEXPROPHET_CODEX_HOME"):
        env["CODEX_HOME"] = config["CODEXPROPHET_CODEX_HOME"]
    return env


def run(
    cmd: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)


def auth_mode(status_text: str) -> str:
    lower = status_text.lower()
    if "logged in using chatgpt" in lower:
        return "chatgpt"
    if "api key" in lower:
        return "api_key"
    if "not logged in" in lower:
        return "not_authenticated"
    return "unknown"


def probe_chatgpt_auth(config: dict[str, str], *, use_default_home: bool = False) -> dict[str, object]:
    env = codex_env(config, use_default_home=use_default_home)
    status = run(["codex", "login", "status"], env=env, timeout_seconds=30)
    status_text = combined_output(status)
    mode = auth_mode(status_text)
    probe_text = ""
    probe_ok = False
    if status.returncode == 0 and mode == "chatgpt":
        model = config.get("CODEX_AUTH_WATCHDOG_MODEL") or config.get("CODEX_FORECAST_MODEL") or "gpt-5.5"
        probe = run(
            ["codex", "exec", "--model", model, "--sandbox", "read-only", "Reply exactly OK"],
            env=env,
            timeout_seconds=int(config.get("CODEX_AUTH_WATCHDOG_PROBE_TIMEOUT_SECONDS") or "150"),
        )
        probe_text = combined_output(probe)
        probe_ok = probe.returncode == 0 and "OK" in probe.stdout
    return {
        "status_returncode": status.returncode,
        "status_text": status_text,
        "mode": mode,
        "probe_ok": probe_ok,
        "probe_output_tail": probe_text[-2000:],
        "healthy": mode == "chatgpt" and probe_ok,
    }


def copy_default_chatgpt_auth(config: dict[str, str], repairs: list[str]) -> bool:
    target_home = Path(config.get("CODEXPROPHET_CODEX_HOME") or "")
    default_home = Path.home() / ".codex"
    if not target_home or target_home.resolve() == default_home.resolve():
        repairs.append("default auth copy skipped: CodexProphet uses the default CODEX_HOME")
        return False
    source = default_home / "auth.json"
    target = target_home / "auth.json"
    if not source.exists():
        repairs.append("default auth copy skipped: ~/.codex/auth.json missing")
        return False
    default_probe = probe_chatgpt_auth(config, use_default_home=True)
    if not default_probe["healthy"]:
        repairs.append("default auth copy skipped: default Codex home is not healthy ChatGPT auth")
        return False
    target_home.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_suffix(
            f".json.bak-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        shutil.copy2(target, backup)
        repairs.append(f"backed up existing CodexProphet auth to {backup}")
    shutil.copy2(source, target)
    repairs.append("copied verified ChatGPT auth from default Codex home into CodexProphet CODEX_HOME")
    return True


def attempt_device_login(config: dict[str, str], repairs: list[str]) -> bool:
    env = codex_env(config)
    try:
        result = run(
            ["codex", "login", "--device-auth"],
            env=env,
            timeout_seconds=int(config.get("CODEX_AUTH_WATCHDOG_DEVICE_LOGIN_TIMEOUT_SECONDS") or "180"),
        )
    except subprocess.TimeoutExpired:
        repairs.append("device-auth login timed out before completing")
        return False
    output = combined_output(result)
    repairs.append(f"device-auth login exited {result.returncode}: {output[-1000:]}")
    return result.returncode == 0


def send_email(config: dict[str, str], subject: str, body: str) -> tuple[bool, str]:
    recipient = (
        config.get("CODEX_AUTH_WATCHDOG_EMAIL_TO")
        or config.get("CODEX_OPENCLAW_OBSERVER_FAILURE_EMAIL_TO")
        or config.get("SEC_EMAIL")
        or "wenhanson0@gmail.com"
    )
    account = config.get("CODEX_AUTH_WATCHDOG_EMAIL_ACCOUNT") or config.get(
        "CODEX_OPENCLAW_OBSERVER_FAILURE_EMAIL_ACCOUNT", "wenhanson0@gmail.com"
    )
    result = subprocess.run(
        [
            "gog",
            "gmail",
            "send",
            "--account",
            account,
            "--to",
            recipient,
            "--subject",
            subject,
            "--body-file",
            "-",
        ],
        input=body,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    return result.returncode == 0, combined_output(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    config = parse_env_file(ENV_FILE)
    started = dt.datetime.now(dt.timezone.utc)
    initial = probe_chatgpt_auth(config)
    repairs: list[str] = []
    repaired = False
    final = initial

    if not initial["healthy"]:
        repaired = copy_default_chatgpt_auth(config, repairs)
        final = probe_chatgpt_auth(config)
        if not final["healthy"]:
            repaired = attempt_device_login(config, repairs) or repaired
            final = probe_chatgpt_auth(config)

    healthy = bool(final["healthy"])
    subject_state = "OK ChatGPT OAuth" if healthy else "NEEDS ATTENTION"
    subject = f"CodexProphet auth watchdog: {subject_state}"
    body_lines = [
        f"CodexProphet auth watchdog report",
        f"Time: {started.astimezone().isoformat(timespec='seconds')}",
        f"Host: {socket.gethostname()}",
        f"Repo: {ROOT}",
        f"CODEX_HOME: {config.get('CODEXPROPHET_CODEX_HOME') or os.environ.get('CODEX_HOME') or '~/.codex'}",
        "",
        f"Final status: {'healthy' if healthy else 'unhealthy'}",
        f"Final auth mode: {final['mode']}",
        f"Final probe OK: {final['probe_ok']}",
        "",
        f"Initial auth mode: {initial['mode']}",
        f"Initial probe OK: {initial['probe_ok']}",
        f"Repair attempted: {bool(repairs)}",
        f"Repair changed auth: {repaired}",
        "",
        "Policy: CodexProphet must use Codex ChatGPT login/OAuth only. API-key and access-token env vars are stripped during this check; API fallback is not used.",
    ]
    if repairs:
        body_lines.extend(["", "Repair log:", *[f"- {item}" for item in repairs]])
    if final["probe_output_tail"] and not healthy:
        body_lines.extend(["", "Probe output tail:", str(final["probe_output_tail"])])
    body = "\n".join(body_lines) + "\n"

    email_requested = not args.no_email and not healthy
    email_ok = True
    email_output = "skipped-success"
    if email_requested:
        email_ok, email_output = send_email(config, subject, body)

    event = {
        "time": started.isoformat(),
        "healthy": healthy,
        "initial": initial,
        "final": final,
        "repairs": repairs,
        "email_requested": email_requested,
        "email_ok": email_ok,
        "email_output_tail": email_output[-1000:],
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")

    if args.json:
        print(json.dumps(event, indent=2, sort_keys=True))
    else:
        print(body)
        if not email_requested:
            print("Email: skipped because watchdog is healthy")
        else:
            print(f"Email: {'sent' if email_ok else 'failed'}")
    if not email_ok:
        return 2
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
