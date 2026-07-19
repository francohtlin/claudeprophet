from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_noninteractive_forecast_launcher_defaults_to_network_capable_permission_mode() -> None:
    script = (ROOT / "scripts" / "run_goal_exec.sh").read_text(encoding="utf-8")

    assert 'PERMISSION_MODE="${CLAUDE_FORECAST_PERMISSION_MODE:-bypassPermissions}"' in script


def test_noninteractive_forecast_launcher_drives_claude_cli() -> None:
    script = (ROOT / "scripts" / "run_goal_exec.sh").read_text(encoding="utf-8")

    assert "claude \\" in script
    assert "--print" in script
    assert '--permission-mode "$PERMISSION_MODE"' in script
    assert "codex exec" not in script


def test_noninteractive_forecast_launcher_requires_subscription_auth() -> None:
    script = (ROOT / "scripts" / "run_goal_exec.sh").read_text(encoding="utf-8")

    assert "ensure_claude_auth" in script
    assert "configure_claude_home" in script
    assert "load_secret_from_env_file CLAUDEPROPHET_CLAUDE_CONFIG_DIR" in script
    assert 'export CLAUDE_CONFIG_DIR="$CLAUDEPROPHET_CLAUDE_CONFIG_DIR"' in script
    assert "disable_api_key_auth_env" in script
    assert "unset ANTHROPIC_API_KEY" in script
    assert "unset ANTHROPIC_AUTH_TOKEN" in script
    assert "CLAUDE_CODE_OAUTH_TOKEN" in script
    assert "ANTHROPIC_API_KEY auth is disabled for forecasts" in script
    assert "codex login" not in script
    assert "OPENAI_API_KEY" not in script


def test_noninteractive_forecast_launcher_requires_explicit_openrouter_fallback() -> None:
    script = (ROOT / "scripts" / "run_goal_exec.sh").read_text(encoding="utf-8")

    assert "run_openrouter_fallback" in script
    assert "load_secret_from_env_file OPENROUTER_API_KEY" in script
    assert "scripts/openrouter_fallback.py" not in script
    assert "$SCRIPT_DIR/openrouter_fallback.py" in script
    assert "Claude run failed with exit code" in script
    assert "OpenRouter fallback is disabled by default" in script
    assert "CLAUDE_ALLOW_OPENROUTER_FALLBACK" in script


def test_env_example_documents_network_capable_forecast_permission_mode() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "CLAUDE_FORECAST_PERMISSION_MODE=bypassPermissions" in env_example


# --- Production deploy scaffolding (unchanged Codex-based Mac-mini setup) ---
# These scripts are the original authors' production deployment and were left as
# they were during the Claude migration; the assertions below pin their existing
# behavior so a future edit is a conscious choice.


def test_codex_auth_watchdog_reports_chatgpt_policy() -> None:
    script = (ROOT / "scripts" / "codex_auth_watchdog.py").read_text(encoding="utf-8")

    assert "OPENAI_API_KEY" in script
    assert "CODEX_ACCESS_TOKEN" in script
    assert "env.pop" in script
    assert "logged in using chatgpt" in script
    assert '"codex", "exec"' in script
    assert "gog" in script
    assert "CodexProphet must use Codex ChatGPT login/OAuth only" in script
    assert "email_requested = not args.no_email and not healthy" in script
    assert "Email: skipped because watchdog is healthy" in script


def test_auth_watchdog_uses_launchd_not_openclaw_cron() -> None:
    script = (ROOT / "scripts" / "install_auth_watchdog_launchd.sh").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts" / "deploy_mac_mini.sh").read_text(encoding="utf-8")

    assert "StartInterval" in script
    assert "scripts/codex_auth_watchdog.py --json" in script
    assert "launchctl bootstrap" in script
    assert "openclaw cron" not in script
    assert "install_auth_watchdog_launchd.sh" in deploy


def test_mac_mini_deploy_refuses_to_restart_active_forecasts() -> None:
    script = (ROOT / "scripts" / "deploy_mac_mini.sh").read_text(encoding="utf-8")

    assert "active_forecasts" in script
    assert "Refusing to deploy" in script
    assert "CODEXPROPHET_FORCE_DEPLOY" in script
    assert "scripts/run_goal_exec.sh|codex exec" in script
