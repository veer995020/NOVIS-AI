"""Imports: every module in the backend package imports cleanly, with no cycles or missing names."""
import importlib
import pkgutil

import backend


def _backend_modules() -> list[str]:
    names = []
    for info in pkgutil.walk_packages(backend.__path__, prefix="backend."):
        if info.name.startswith("backend.tests"):
            continue
        names.append(info.name)
    return names


def test_every_backend_module_imports() -> None:
    modules = _backend_modules()
    assert len(modules) > 20
    for name in modules:
        importlib.import_module(name)
