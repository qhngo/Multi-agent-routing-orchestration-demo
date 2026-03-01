from __future__ import annotations

import importlib
import pkgutil

from fastapi import APIRouter

from src.app.api.app_context import AppContext


def load_routers(package_name: str, context: AppContext) -> list[APIRouter]:
    """Discover and load all route modules exposing build_router(context)."""
    routers: list[APIRouter] = []
    package = importlib.import_module(package_name)

    for module_info in sorted(
        pkgutil.iter_modules(package.__path__, prefix=f"{package_name}."),
        key=lambda item: item.name,
    ):
        # Ignore infrastructure modules in the routes package.
        if module_info.name.endswith(".loader"):
            continue
        module = importlib.import_module(module_info.name)
        build_router = getattr(module, "build_router", None)
        if not callable(build_router):
            continue
        router = build_router(context)
        if not isinstance(router, APIRouter):
            raise TypeError(f"{module_info.name}.build_router must return APIRouter")
        context.logger.info("Loaded router module '%s'.", module_info.name)
        routers.append(router)

    return routers
