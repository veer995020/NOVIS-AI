"""Provider registry and the generic secret wrapper.

No production provider is implemented in this build, so the registry must be empty
in every environment. A test that expected generated text would be a fake success.
"""
import pytest

from backend.config import Secret, Settings
from backend.services.providers import default_providers


@pytest.mark.parametrize("environment", ["development", "test", "production"])
def test_no_provider_is_registered_in_any_environment(environment: str) -> None:
    settings = Settings(environment=environment, cors_origins=("https://app.example.com",))
    assert dict(default_providers(settings)) == {}


def test_secret_is_not_shown_in_repr_or_str() -> None:
    secret = Secret("internal-value-123")
    assert "internal-value-123" not in repr(secret)
    assert "internal-value-123" not in str(secret)
    assert secret.reveal() == "internal-value-123"
