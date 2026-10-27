"""Run with mac/.venv/bin/python; no gated model weights or network required."""

import os
import pickle
import tempfile
from dataclasses import replace
from pathlib import Path


def main():
    os.environ.setdefault(
        "NOX_VOICE_WEIGHTS_DIR", str(Path(__file__).resolve().parents[2] / "mac" / "weights")
    )
    os.environ["HF_HUB_OFFLINE"] = "1"
    import mlx.core as mx
    import mlx.nn as nn
    import numpy as np
    import torch
    from engine import inference
    from engine.voices import load_embeddings
    from moshi_mlx.models.lm import Lm

    config = inference.get_config()
    small_transformer = replace(
        config.transformer,
        d_model=64,
        num_heads=4,
        num_layers=1,
        dim_feedforward=384,
        context=64,
        max_seq_len=64,
    )
    small_depformer = replace(
        config.depformer,
        transformer=replace(small_transformer, positional_embedding="none", context=8),
    )
    small = replace(
        config,
        transformer=small_transformer,
        depformer=small_depformer,
        text_in_vocab_size=33,
        text_out_vocab_size=32,
        audio_vocab_size=33,
    )
    for bits in (0, 4, 5, 6, 8):
        model = Lm(small)
        if bits:
            nn.quantize(model, group_size=64, bits=bits)
        model.warmup()
        print(f"PASS: small MLX model warmup, precision={bits or 'full'}")

    model = Lm(small)
    inference.MODEL = model
    inference.forward_embeddings(mx.zeros((1, 1, 64)))
    inference.save_cache_state()
    saved_offset = model.transformer_cache[0].self_attn.offset
    inference.forward_embeddings(mx.ones((1, 1, 64)))
    inference.restore_cache_state()
    assert model.transformer_cache[0].self_attn.offset == saved_offset
    print("PASS: voice cache snapshot and restoration")

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "voice.pt"
        expected = torch.randn(2, 1, 1, 4096)
        torch.save({"embeddings": expected, "cache": torch.zeros(1)}, path)
        assert np.array_equal(load_embeddings(path), expected.numpy())

        class UnsafeObject:
            def __reduce__(self):
                return eval, ("42",)

        torch.save({"embeddings": UnsafeObject()}, path)
        try:
            load_embeddings(path)
        except pickle.UnpicklingError:
            print("PASS: pickle code execution payload rejected")
        else:
            raise AssertionError("Unsafe checkpoint was not rejected")
    print("Native smoke checks passed. The real 7B model and codec are not loaded by this test.")


if __name__ == "__main__":
    main()
