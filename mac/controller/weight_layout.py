"""Explicit, offline migration of legacy weights; never run during controller reads/startup."""

from contextlib import ExitStack, suppress
from pathlib import Path

from engine.lfm3.assets import asset_lock

from .model_files import ASSETS, asset_root, digest

LEGACY_PERSONAPLEX = (
    *ASSETS,
    "config.json",
    "model_mlx.safetensors",
    "voices_cpu",
    "manifest.json",
    ".cache",
)


def migrate_weights(weights_dir: Path) -> int:
    """Move known legacy entries without downloads or overwrites. Stop the controller first.

    Check all conflicts before moving anything. Identical regular files can be
    consolidated; different files and nonempty destination directories block the
    migration. An interrupted migration can be rerun.
    """
    weights_dir = weights_dir.resolve()
    personaplex = asset_root(weights_dir)
    providers = weights_dir / "providers"
    legacy_lfm3 = providers / "lfm3-audio"
    candidates = [(weights_dir / name, personaplex / name) for name in LEGACY_PERSONAPLEX]
    candidates.append((legacy_lfm3, weights_dir / "lfm3-audio"))
    moves = []
    with ExitStack() as locks:
        for source, target in candidates:
            if source.is_symlink() or target.is_symlink():
                raise ValueError(f"Asset migration does not follow symlinks: {source.name}.")
            if not source.exists():
                continue
            if not source.is_file() and not source.is_dir():
                raise ValueError(f"Unsupported asset entry: {source.name}.")
            for parent in (source.parent, target.parent):
                if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                    raise ValueError("Asset migration requires real provider directories.")
            duplicate = False
            empty_directory = target.is_dir() and not any(target.iterdir())
            if target.exists():
                duplicate = (
                    source.is_file()
                    and target.is_file()
                    and source.stat().st_size == target.stat().st_size
                    and digest(source) == digest(target)
                )
                if not duplicate and not (source.is_dir() and empty_directory):
                    raise ValueError(f"Asset migration conflict at {target}; no files were moved.")
            if source == legacy_lfm3:
                if not source.is_dir():
                    raise ValueError("The legacy LFM3 installation must be a directory.")
                # The same lock used by preparation/inference blocks moving an active runtime.
                locks.enter_context(asset_lock(source))
            moves.append((source, target, duplicate, empty_directory))
        for source, target, duplicate, empty_directory in moves:
            if duplicate:
                source.unlink()
            else:
                if empty_directory:
                    target.rmdir()
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
        if moves:
            with suppress(OSError):
                providers.rmdir()  # Remove only the now-empty legacy container.
    return len(moves)
