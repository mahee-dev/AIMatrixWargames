"""I-142 — only loadable prompts sit at the top of ``docs/prompts/`` and ship in the wheel.

Every prompt version is kept, because run manifests seal the version and its hash (agents.md
§31). A version the code can no longer load lives in ``docs/prompts/superseded/``, unchanged,
and is not bundled into ``amaw_resources`` (D-103). Test IDs ``TEST-I142-001/002/003`` are the
task's own (I-142 in ``dev/BACKLOG_RESOLVED.md``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from agents.consequence import CONSEQUENCE_PROMPT_VERSIONS
from agents.player import PLAYER_PROMPT_VERSIONS, REPLY_PLAYER_PROMPT_VERSIONS
from agents.umpire import UMPIRE_PROMPT_VERSIONS


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "docs" / "prompts"
SUPERSEDED = PROMPTS / "superseded"
BUNDLED = ROOT / "amaw_resources" / "prompts"

# The superseded prompts as they were when they stopped being loadable. They are sealed
# experimental conditions, so moving them must not change a byte.
SUPERSEDED_SHA256 = {
    "player-v1.1": "e7ee4953d5e7e593f60be0d520a9b52e7fd73df7ed88bd8e12fe2b145b90241a",
    "player-v1.2": "5c881863abf4a217ef73ed2ccc67779d685322f8722147ddcbbb98b44d43edca",
    "player-v1.3": "e82c9fe927f190826796bedc45461e88a5cc557ba7974e7e359a00094e34f955",
    "player-v1.4": "cb9099c3b8a8d9d094e0f622066ff3d1da9e5844c0b5cad13d52a253e3781357",
    "player-v1.5": "dfd36a8e1ac47dd6f4fce5ede340e81a5d004c19e092453f59ab9c174586a3da",
    "player-v1.6": "462cc1eee9b44ef7a6a4c1bbc52921ae1a39530b62c2ce6698989d5949119257",
    "player-v1.7": "69e9400e5b134c08492991260c8825906632752062c9ed2239f4ea35386199d0",
    "player-v1.8": "6027ab5258754a9438a137bba31ac53f60a78086debdfeb7561150bc05b4ae76",
    "player-v1.9": "f61829a7d40e38ce5b52ecd0a3f36db60f52b4d1d37396395aeda4d2c45ee789",
    "umpire-v1.1": "70adcf231de5711bba79daeef0b64b4741606a9bc4f348852a4e37d3196781bf",
    "umpire-v1.2": "b5b6252de1db3fbc0a50cc8c4dcc0d285324d726e693bfd87599e5a0681cedde",
}
UNWIRED = {"evaluator-v1.1"}  # optional T-061; not superseded, never bundled


def _loadable() -> set[str]:
    return (
        set(PLAYER_PROMPT_VERSIONS.values())
        | set(REPLY_PLAYER_PROMPT_VERSIONS.values())
        | set(UMPIRE_PROMPT_VERSIONS)
        | set(CONSEQUENCE_PROMPT_VERSIONS)
    )


def _stems(directory: Path) -> set[str]:
    return {p.stem for p in directory.glob("*.md") if p.name != "README.md"}


def test_i142_001_superseded_prompts_move_unchanged_out_of_the_top_level() -> None:
    assert _stems(PROMPTS) == _loadable() | UNWIRED
    assert _stems(SUPERSEDED) == set(SUPERSEDED_SHA256)
    assert not _loadable() & set(SUPERSEDED_SHA256)
    for version, digest in SUPERSEDED_SHA256.items():
        data = (SUPERSEDED / f"{version}.md").read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, f"{version} changed while moving"


def test_i142_002_the_wheel_bundles_exactly_the_loadable_prompts() -> None:
    assert _stems(BUNDLED) == _loadable()
    for version in _loadable():
        bundled = (BUNDLED / f"{version}.md").read_bytes()
        assert bundled == (PROMPTS / f"{version}.md").read_bytes(), f"{version} drifted"


def test_i142_003_prompt_index_names_superseded_files_by_their_path() -> None:
    index = (PROMPTS / "README.md").read_text(encoding="utf-8")
    for version in SUPERSEDED_SHA256:
        assert f"`superseded/{version}.md`" in index, f"README does not list superseded/{version}.md"
