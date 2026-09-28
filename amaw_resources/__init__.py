"""Access AMAW's wheel-packaged runtime prompts and supported scenarios.

Callers use this module instead of knowing where package data lives.  Scenario resources are
materialised only for the lifetime of the returned context manager, which also works when the
package is imported from a non-filesystem loader.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path


_PACKAGE = __name__
_PROMPTS = resources.files(_PACKAGE).joinpath("prompts")
_SCENARIOS = resources.files(_PACKAGE).joinpath("scenarios")
SUPPORTED_BUNDLED_SCENARIOS = (
    "northern-strait",
    "orison-crisis",
    "swedish-government",
)


class RuntimeResourceError(ValueError):
    """A requested packaged runtime asset does not exist or is not a valid asset name."""


def _plain_name(value: str, *, kind: str) -> str:
    if not value or Path(value).name != value or value in {".", ".."}:
        raise RuntimeResourceError(f"invalid {kind} name {value!r}")
    return value


def read_prompt(version: str) -> str:
    """Read one versioned runtime prompt as UTF-8 text."""
    name = _plain_name(version, kind="prompt version")
    prompt = _PROMPTS.joinpath(f"{name}.md")
    if not prompt.is_file():
        raise RuntimeResourceError(f"no bundled prompt {version!r}")
    return prompt.read_text(encoding="utf-8")


def _walk_files(root, prefix: str = "") -> Iterator[tuple[str, bytes]]:  # noqa: ANN001
    for child in root.iterdir():
        relative = f"{prefix}/{child.name}" if prefix else child.name
        if child.is_dir():
            yield from _walk_files(child, relative)
        elif child.is_file():
            yield relative, child.read_bytes()


def _require_bundled(name: str) -> str:
    scenario_name = _plain_name(name, kind="scenario")
    if scenario_name not in SUPPORTED_BUNDLED_SCENARIOS or not _SCENARIOS.joinpath(
        scenario_name
    ).is_dir():
        raise RuntimeResourceError(f"no bundled scenario {name!r}")
    return scenario_name


def scenario_files(name: str) -> dict[str, bytes]:
    """Return the relative file names and exact bytes of one bundled scenario."""
    scenario = _SCENARIOS.joinpath(_require_bundled(name))
    return dict(_walk_files(scenario))


@contextmanager
def bundled_scenario(name: str) -> Iterator[Path]:
    """Yield a filesystem view of one bundled scenario for the context lifetime."""
    scenario = _SCENARIOS.joinpath(_require_bundled(name))
    with resources.as_file(scenario) as materialized:
        yield materialized


def _looks_like_explicit_path(value: str, supplied: Path, candidate: Path) -> bool:
    return supplied.is_absolute() or len(supplied.parts) > 1 or candidate.exists()


def _checkout_scenarios() -> dict[str, Path]:
    """Scenario packages under ``scenarios/`` in the working directory, by directory name."""
    root = Path.cwd() / "scenarios"
    if not root.is_dir():
        return {}
    return {
        child.name: child.resolve()
        for child in sorted(root.iterdir())
        if child.is_dir() and child.joinpath("scenario.yaml").is_file()
    }


def available_scenarios() -> list[str]:
    """Every name ``resolve_scenario`` accepts here: bundled names, then checkout packages."""
    checkout = [name for name in _checkout_scenarios() if name not in SUPPORTED_BUNDLED_SCENARIOS]
    return [*SUPPORTED_BUNDLED_SCENARIOS, *checkout]


@contextmanager
def resolve_scenario(value: str) -> Iterator[Path]:
    """Resolve an explicit scenario directory, a bundled name, or a checkout package name.

    A filesystem directory supplied by the user always wins, including when its final component
    shadows a bundled name.  An explicit but invalid path is never silently replaced by packaged
    data.  A bare name resolves to the bundled package first, so a bundled name means the same
    content wherever it runs; otherwise to ``scenarios/<name>/`` under the working directory, so
    every scenario in a checkout runs by name (D-115).
    """
    supplied = Path(value).expanduser()
    candidate = supplied if supplied.is_absolute() else Path.cwd() / supplied
    candidate = candidate.resolve()
    if candidate.is_dir() and candidate.joinpath("scenario.yaml").is_file():
        yield candidate
        return
    if _looks_like_explicit_path(value, supplied, candidate):
        raise RuntimeResourceError(
            f"{value!r} is not a scenario package directory containing scenario.yaml"
        )
    name = _plain_name(value, kind="scenario")
    if name in SUPPORTED_BUNDLED_SCENARIOS:
        with bundled_scenario(name) as packaged:
            yield packaged
        return
    checkout = _checkout_scenarios().get(name)
    if checkout is None:
        raise RuntimeResourceError(
            f"no scenario {value!r}; available: {', '.join(available_scenarios())}, "
            "or a path to a scenario package directory"
        )
    yield checkout


__all__ = [
    "RuntimeResourceError",
    "SUPPORTED_BUNDLED_SCENARIOS",
    "available_scenarios",
    "bundled_scenario",
    "read_prompt",
    "resolve_scenario",
    "scenario_files",
]
