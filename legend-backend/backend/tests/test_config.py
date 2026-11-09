"""Configuration tests use only the standard library, so they also run on Python 3.15."""
import pytest

from backend.config import (
    ConfigError,
    Secret,
    Settings,
    load_settings,
    parse_model_routes,
    validate_settings,
)


def test_defaults_are_secure() -> None:
    settings = load_settings({})
    assert settings.environment == "development"
    assert settings.enable_docs is False
    assert settings.cors_origins == ()
    assert settings.model_routes == ()
    assert settings.log_format == "json"


def test_reads_environment_values() -> None:
    settings = load_settings(
        {
            "APP_ENV": "production",
            "API_ACCESS_KEY": "qa-access-key-123",
            "ALLOWED_HOSTS": "api.example.com",
            "CORS_ORIGINS": "https://app.example.com, https://chat.example.com",
            "MODEL_ROUTES": "rapid=example:model-1",
            "LOG_LEVEL": "warning",
            "MAX_REQUEST_BYTES": "4096",
        }
    )
    assert settings.environment == "production"
    assert settings.cors_origins == ("https://app.example.com", "https://chat.example.com")
    assert settings.model_routes == (("rapid", "example", "model-1"),)
    assert settings.log_level == "WARNING"
    assert settings.max_request_bytes == 4096
    assert settings.allowed_hosts == ("api.example.com",)
    assert settings.api_access_key is not None


def test_route_parsing_accepts_several_entries() -> None:
    bindings, problems = parse_model_routes("rapid=example:model-1, sonet=example:model-2")
    assert problems == []
    assert bindings == (("rapid", "example", "model-1"), ("sonet", "example", "model-2"))


def test_malformed_route_entry_is_reported() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({"MODEL_ROUTES": "sonet-example"})
    assert any("MODEL_ROUTES" in problem for problem in info.value.problems)


def test_unknown_model_in_routes_is_rejected() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({"MODEL_ROUTES": "gpt-9=example:model-1"})
    assert any("unknown model" in problem for problem in info.value.problems)


def test_model_routed_twice_is_rejected() -> None:
    with pytest.raises(ConfigError) as info:
        validate_settings(Settings(model_routes=(("rapid", "example", "a"), ("rapid", "example", "b"))))
    assert any("more than once" in problem for problem in info.value.problems)


def test_invalid_provider_model_name_is_rejected() -> None:
    with pytest.raises(ConfigError):
        validate_settings(Settings(model_routes=(("rapid", "example", "has space"),)))


def test_rejects_wildcard_cors() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({"CORS_ORIGINS": "*"})
    assert any("'*'" in problem for problem in info.value.problems)


def test_rejects_origin_with_path() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({"CORS_ORIGINS": "https://a.example.com/path"})
    assert any("path" in problem for problem in info.value.problems)


def test_production_requires_https_origins() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({"APP_ENV": "production", "CORS_ORIGINS": "http://insecure.example.com"})
    assert any("https" in problem for problem in info.value.problems)


def test_production_rejects_docs() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({"APP_ENV": "production", "ENABLE_DOCS": "true"})
    assert any("ENABLE_DOCS" in problem for problem in info.value.problems)


def test_reports_all_problems_at_once() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({"APP_ENV": "staging", "LOG_LEVEL": "loud", "ENABLE_DOCS": "maybe"})
    assert len(info.value.problems) == 3


def test_secret_wrapper_is_redacted_except_when_revealed() -> None:
    secret = Secret("internal-value-123")
    assert "internal-value-123" not in repr(secret)
    assert "internal-value-123" not in str(secret)
    assert secret.reveal() == "internal-value-123"


def test_context_window_cannot_exceed_history() -> None:
    with pytest.raises(ConfigError):
        validate_settings(Settings(max_history_messages=5, max_context_messages=10))


def test_integer_out_of_range_is_reported() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({"MAX_REQUEST_BYTES": "10"})
    assert any("MAX_REQUEST_BYTES" in problem for problem in info.value.problems)


def test_modes_must_include_required_modes() -> None:
    with pytest.raises(ConfigError) as info:
        validate_settings(Settings(modes=("research", "coding")))
    assert any("general" in problem for problem in info.value.problems)
    assert any("writing" in problem for problem in info.value.problems)


def test_generation_timeout_is_bounded() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings({"GENERATION_TIMEOUT_SECONDS": "0"})
    assert any("GENERATION_TIMEOUT_SECONDS" in problem for problem in info.value.problems)
    assert load_settings({"GENERATION_TIMEOUT_SECONDS": "30"}).generation_timeout_seconds == 30


def test_conversation_idle_limit_is_bounded() -> None:
    with pytest.raises(ConfigError):
        load_settings({"CONVERSATION_IDLE_SECONDS": "10"})
    assert load_settings({"CONVERSATION_IDLE_SECONDS": "120"}).conversation_idle_seconds == 120


def test_rate_limit_range_is_enforced() -> None:
    import pytest as _pytest

    from backend.config import ConfigError as _ConfigError

    with _pytest.raises(_ConfigError):
        load_settings({"RATE_LIMIT_PER_MINUTE": "-5"})
    assert load_settings({"RATE_LIMIT_PER_MINUTE": "0"}).rate_limit_per_minute == 0
