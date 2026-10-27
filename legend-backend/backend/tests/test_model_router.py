import pytest

from backend.config import DEFAULT_MODEL, ConfigError, Settings
from backend.main import create_app
from backend.services.model_router import (
    ModelUnavailableError,
    build_model_router,
    resolve_model_id,
)
from backend.tests.fakes import FAKE_PROVIDER, ROUTES, replying


def test_missing_model_resolves_to_rapid() -> None:
    assert resolve_model_id(None) == DEFAULT_MODEL == "rapid"
    assert resolve_model_id("sonet") == "sonet"


def test_unrouted_model_raises_unavailable() -> None:
    router = build_model_router(ROUTES[:1], {FAKE_PROVIDER: replying("x")})
    with pytest.raises(ModelUnavailableError) as info:
        router.route_for("sonet")
    assert info.value.model_id == "sonet"


def test_available_models_follow_catalog_order() -> None:
    bindings = (("sonet", FAKE_PROVIDER, "m1"), ("brainstormer", FAKE_PROVIDER, "m2"))
    router = build_model_router(bindings, {FAKE_PROVIDER: replying("x")})
    assert router.available_models() == ("brainstormer", "sonet")


def test_route_names_the_provider_model() -> None:
    provider = replying("x")
    router = build_model_router((("mythical", FAKE_PROVIDER, "provider-model-7"),), {FAKE_PROVIDER: provider})
    route = router.route_for("mythical")
    assert route.provider is provider
    assert route.provider_model == "provider-model-7"


def test_route_to_missing_provider_fails_at_startup() -> None:
    with pytest.raises(ConfigError) as info:
        build_model_router((("rapid", "example", "model-1"),), {})
    assert any("example" in problem for problem in info.value.problems)


def test_create_app_fails_fast_on_a_route_to_an_unregistered_provider() -> None:
    settings = Settings(model_routes=(("rapid", "example", "model-1"),))
    with pytest.raises(ConfigError):
        create_app(settings)
