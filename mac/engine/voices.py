"""Restricted voice tensor loader, separated for testing without MLX."""

from pathlib import Path


def load_embeddings(path: Path):
    import torch

    state = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(state, dict) or not isinstance(state.get("embeddings"), torch.Tensor):
        raise ValueError("Voice file must contain tensor embeddings.")
    embeddings = state["embeddings"]
    if (
        embeddings.ndim != 4
        or tuple(embeddings.shape[1:]) != (1, 1, 4096)
        or not 1 <= embeddings.shape[0] <= 1024
    ):
        raise ValueError("Voice embeddings have an unsupported shape.")
    if not embeddings.is_floating_point() or not torch.isfinite(embeddings).all():
        raise ValueError("Voice embeddings must contain finite floating point values.")
    return embeddings.float().numpy()
