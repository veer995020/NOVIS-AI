"""Dependencies: installed versions match the pins, and the runtime set is minimal."""
from importlib import metadata
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _pins(filename: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in (BACKEND / filename).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-r")):
            continue
        name, _, version = line.partition("==")
        pins[name.strip().lower()] = version.strip()
    return pins


def test_runtime_pins_match_installed_versions() -> None:
    for name, version in _pins("requirements.txt").items():
        assert metadata.version(name) == version, name


def test_runtime_dependencies_are_only_the_three_required() -> None:
    assert set(_pins("requirements.txt")) == {"fastapi", "pydantic", "uvicorn"}


def test_dev_pins_match_installed_versions() -> None:
    for name, version in _pins("requirements-dev.txt").items():
        assert metadata.version(name) == version, name
