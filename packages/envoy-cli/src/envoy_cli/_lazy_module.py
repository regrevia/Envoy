"""Import proxies that defer a heavy dependency until it is actually used.

``reader`` imported ``httpx`` and ``pypdf`` at module scope, so every Envoy
invocation paid for both even when it only needed the argument parser or
answered a version question. ``lazy_module`` defers a whole module and
``lazy_attr`` defers one name from one; access imports on demand and the import
still happens exactly once, so behaviour is unchanged for code that uses them.
"""

from __future__ import annotations

import importlib
from typing import Any


class _LazyModule:
    """Proxy that imports ``name`` the first time an attribute is read."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)

    def _target(self) -> Any:
        return importlib.import_module(self._name)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._target(), item)

    def __setattr__(self, key: str, value: Any) -> None:
        setattr(self._target(), key, value)

    def __repr__(self) -> str:
        return f"<lazy module {self._name!r}>"


class _LazyAttr:
    """Proxy that imports ``module`` and resolves ``name`` on first use."""

    __slots__ = ("_module", "_name")

    def __init__(self, module: str, name: str) -> None:
        object.__setattr__(self, "_module", module)
        object.__setattr__(self, "_name", name)

    def _target(self) -> Any:
        return getattr(importlib.import_module(self._module), self._name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._target()(*args, **kwargs)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._target(), item)

    def __repr__(self) -> str:
        return f"<lazy {self._module}.{self._name}>"


def lazy_module(name: str) -> _LazyModule:
    return _LazyModule(name)


def lazy_attr(module: str, name: str) -> _LazyAttr:
    return _LazyAttr(module, name)
