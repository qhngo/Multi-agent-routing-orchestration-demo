from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Callable

from src.app.config.settings import AppSettings
from src.app.runtime.interface import RuntimeAgentSpec


def discover_runtime_agent_specs(
    settings: AppSettings,
    logger: logging.Logger,
) -> list[RuntimeAgentSpec]:
    package_name = "src.app.runtime"
    package = importlib.import_module(package_name)
    specs: list[RuntimeAgentSpec] = []

    for module_info in sorted(
        pkgutil.iter_modules(package.__path__, prefix=f"{package_name}."),
        key=lambda item: item.name,
    ):
        module = importlib.import_module(module_info.name)
        builder = getattr(module, "build_agent_specs", None)
        if not callable(builder):
            continue

        logger.info("Loading runtime agent specs from module '%s'.", module_info.name)
        built = _call_builder(builder, settings)
        specs.extend(built)

    if not specs:
        raise ValueError("No runtime agent specs discovered. Define build_agent_specs(...) in runtime modules.")
    return specs


def _call_builder(
    builder: Callable[[AppSettings], list[RuntimeAgentSpec]],
    settings: AppSettings,
) -> list[RuntimeAgentSpec]:
    built = builder(settings)
    if not isinstance(built, list):
        raise TypeError("build_agent_specs(...) must return list[RuntimeAgentSpec].")
    return built
