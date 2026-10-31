"""Fingerprint a node function's code, so replay can tell whether a PR changed it.

The fingerprint hashes the function's source text, its defaults, its closure values and
the values of the global names its code uses, following functions, classes and modules
from project files the same way. Library code (under the Python installation, the
virtualenv or the user site-packages) counts by qualified name; any other object by its
class, its repr with memory addresses removed, and the project functions it holds. Source
is read from the file, never from a cache a same-size edit can leave stale, and nothing
depends on paths or line numbers, so a cassette replays from another checkout.

Not seen: what a node reads at run time (files, environment, network), library upgrades,
and changes an object's repr hides. A node that uses a compiled graph, or code with no
readable source, gets no fingerprint (None) and always runs live.
"""

import functools
import hashlib
import re
import site
import sys
from collections.abc import Iterator
from pathlib import Path
from types import CodeType, FunctionType, MethodType, ModuleType

from langgraph.pregel import Pregel

_PREFIXES = (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix)
_LIBRARY = tuple(Path(p).resolve() for p in (*_PREFIXES, site.getusersitepackages()))
_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")
# Class attributes that follow the module name or the file layout, not behaviour.
_CLASS_NOISE = frozenset(
    {
        "__module__",
        "__qualname__",
        "__firstlineno__",
        "__static_attributes__",
        "__dict__",
        "__weakref__",
    }
)


def fingerprint(fn: object) -> str | None:
    """Short hash of a node function's code and what it uses; None when it can't vouch."""
    if not isinstance(fn, FunctionType):
        return None
    try:
        return hashlib.sha256(_function(fn, {id(fn): fn}).encode()).hexdigest()[:16]
    except Exception:  # unreadable source, a repr that raises, ...: never serve on a guess
        return None


def _in_project(path: str | None) -> bool:
    if not path or not Path(path).is_file():
        return False
    resolved = Path(path).resolve()
    return not any(resolved.is_relative_to(root) for root in _LIBRARY)


def _name(value: FunctionType | type) -> str:
    return f"{value.__module__}.{value.__qualname__}"


def _codes(code: CodeType) -> Iterator[CodeType]:
    yield code
    for const in code.co_consts:
        if isinstance(const, CodeType):
            yield from _codes(const)


def _function(fn: FunctionType, seen: dict[int, object]) -> str:
    code = fn.__code__
    lines = Path(code.co_filename).read_text(encoding="utf-8").splitlines()
    end = max((p[1] for c in _codes(code) for p in c.co_positions() if p[1]), default=0)
    names = frozenset(n for c in _codes(code) for n in c.co_names)
    cells = tuple(cell.cell_contents for cell in fn.__closure__ or ())
    values = _describe((fn.__defaults__, fn.__kwdefaults__, cells), names, seen)
    used = [
        f"{n}={_describe(fn.__globals__[n], names, seen)}"
        for n in sorted(names)
        if n in fn.__globals__  # attribute names and builtins are not module globals
    ]
    return "\n".join([*lines[code.co_firstlineno - 1 : end], values, *used])


def _describe(value: object, names: frozenset[str], seen: dict[int, object]) -> str:
    """Stable text for a value a node's code uses; raises when it can't vouch for one."""
    if value is None or isinstance(value, str | bytes | int | float | complex):
        return repr(value)
    if id(value) in seen:
        return "<seen>"
    seen[id(value)] = value  # holding it keeps ids unique while we walk
    if isinstance(value, FunctionType):
        return _function(value, seen) if _in_project(value.__code__.co_filename) else _name(value)
    if isinstance(value, MethodType):
        return _describe((value.__func__, value.__self__), names, seen)
    if isinstance(value, functools.partial):
        return _describe((value.func, value.args, value.keywords), names, seen)
    if isinstance(value, ModuleType):
        if not _in_project(getattr(value, "__file__", None)):
            return value.__name__
        attrs = vars(value)  # only what the code reads from it, e.g. prompts.SYSTEM
        return ";".join(
            f"{n}={_describe(attrs[n], names, seen)}" for n in sorted(names & attrs.keys())
        )
    if isinstance(value, type):
        module = sys.modules.get(value.__module__)  # None: a graph file lgcov loaded itself
        if module is not None and not _in_project(getattr(module, "__file__", None)):
            return _name(value)
        members = {k: v for k, v in vars(value).items() if k not in _CLASS_NOISE}
        return _describe((value.__bases__, members), names, seen)
    if isinstance(value, property):
        return _describe((value.fget, value.fset, value.fdel), names, seen)
    if isinstance(value, Pregel):
        raise TypeError("a compiled graph is not fingerprinted")
    if isinstance(value, list | tuple):
        return "[" + ",".join(_describe(v, names, seen) for v in value) + "]"
    if isinstance(value, set | frozenset):
        return "{" + ",".join(sorted(_describe(v, names, seen) for v in value)) + "}"
    if isinstance(value, dict):
        items = (
            f"{_describe(k, names, seen)}:{_describe(v, names, seen)}" for k, v in value.items()
        )
        return "{" + ",".join(items) + "}"
    # Any other object: its class, its repr, and project code it holds (a @tool's func).
    held = [v for v in getattr(value, "__dict__", {}).values() if isinstance(v, FunctionType)]
    text = _describe(type(value), names, seen) + _ADDRESS.sub("", repr(value))
    return text + _describe(held, names, seen)
