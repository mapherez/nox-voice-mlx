#!/usr/bin/env python3
"""
Convert PersonaPlex PyTorch weights to MLX format.

This script:
1. Loads PersonaPlex safetensors weights
2. Expands depformer from 8 to 16 slices (copies weights 0-7 → 8-15)
3. Remaps weight names to moshi-mlx convention
4. Saves as MLX-compatible safetensors
"""

import argparse
from pathlib import Path
import mlx.core as mx
import numpy as np


def load_safetensors(path: str) -> dict[str, mx.array]:
    """Load safetensors file and return as MLX arrays."""
    return mx.load(path)


def detect_num_slices(state_dict: dict[str, mx.array]) -> int:
    """Detect the number of depformer slices in the weights."""
    max_slice = -1
    for name in state_dict.keys():
        if name.startswith("depformer_in."):
            idx = int(name.split(".")[1])
            max_slice = max(max_slice, idx)
    return max_slice + 1 if max_slice >= 0 else 8


def expand_depformer_weights(
    state_dict: dict[str, mx.array],
    src_slices: int = 8,
    dst_slices: int = 16,
) -> dict[str, mx.array]:
    """
    Expand depformer weights from src_slices to dst_slices by copying.
    PersonaPlex ships with 8 slices but uses 16 at runtime.
    """
    # First detect how many slices we actually have
    actual_slices = detect_num_slices(state_dict)
    if actual_slices >= dst_slices:
        print(f"  Weights already have {actual_slices} slices, no expansion needed.")
        return state_dict

    print(f"  Detected {actual_slices} slices, expanding to {dst_slices}...")
    expanded = dict(state_dict)

    # Groups that need expansion
    to_replace = ["gating", "linears", "depformer_in", "depformer_emb"]

    for name, tensor in list(state_dict.items()):
        # Expand self_attn weights that are chunked
        if "depformer" in name and "self_attn" in name:
            # These are already full-size, may need splitting later
            pass

    # Copy slice 0-7 weights to 8-15
    for old_idx in range(actual_slices):
        new_idx = old_idx + actual_slices
        if new_idx >= dst_slices:
            continue
        for group in to_replace:
            for name, tensor in state_dict.items():
                old_pattern = f"{group}.{old_idx}."
                if old_pattern in name:
                    new_name = name.replace(old_pattern, f"{group}.{new_idx}.")
                    if new_name not in expanded:
                        print(f"  Copying {name} → {new_name}")
                        expanded[new_name] = tensor

    return expanded


def remap_to_mlx(
    state_dict: dict[str, mx.array],
    num_audio_codebooks: int = 16,
    num_depformer_slices: int = 16,
    num_depformer_layers: int = 6,
) -> dict[str, mx.array]:
    """
    Remap PersonaPlex weight names to moshi-mlx convention.

    PersonaPlex (PyTorch) → moshi-mlx (MLX) naming:
    - out_norm.alpha → out_norm.weight (squeeze)
    - emb.{i}.weight → audio_embs.{i}.weight
    - transformer.* → transformer.*
    - depformer_in.{i}.weight → depformer.slices.{i}.linear_in.weight
    - depformer_text_emb.weight → depformer.slices.0.emb.weight
    - depformer_emb.{i}.weight → depformer.slices.{i+1}.emb.weight
    - linears.{i}.weight → depformer.slices.{i}.linear_out.weight
    - depformer.layers.{j}.* → split per slice
    """
    mlx_weights = {}

    for name, tensor in state_dict.items():
        new_name = None
        new_tensor = tensor

        # Output norm: squeeze alpha
        if name == "out_norm.alpha":
            new_name = "out_norm.weight"
            # Alpha is typically [1, 1, dim], squeeze to [dim]
            if tensor.ndim == 3:
                new_tensor = tensor[0, 0]
            elif tensor.ndim == 2:
                new_tensor = tensor[0]

        # Text embeddings (keep as-is)
        elif name.startswith("text_emb.") or name.startswith("text_linear."):
            new_name = name

        # Audio embeddings: emb.{i} → audio_embs.{i}
        elif name.startswith("emb."):
            new_name = name.replace("emb.", "audio_embs.")

        # Main transformer layers
        elif name.startswith("transformer."):
            new_name = name
            # Handle alpha → weight for norms
            if ".alpha" in name:
                new_name = name.replace(".alpha", ".weight")
                if tensor.ndim == 3:
                    new_tensor = tensor[0, 0]
                elif tensor.ndim == 2:
                    new_tensor = tensor[0]
            # Handle in_proj_weight → in_proj.weight
            if ".in_proj_weight" in name:
                new_name = new_name.replace(".in_proj_weight", ".in_proj.weight")

        # Depformer input projections
        elif name.startswith("depformer_in."):
            # depformer_in.{i}.weight → depformer.slices.{i}.linear_in.weight
            parts = name.split(".")
            slice_idx = int(parts[1])
            new_name = f"depformer.slices.{slice_idx}.linear_in.weight"

        # Depformer text embedding (first slice)
        elif name.startswith("depformer_text_emb."):
            # depformer_text_emb.weight → depformer.slices.0.emb.weight
            suffix = name.replace("depformer_text_emb.", "")
            new_name = f"depformer.slices.0.emb.{suffix}"

        # Depformer audio embeddings (slices 1+)
        elif name.startswith("depformer_emb."):
            # depformer_emb.{i}.weight → depformer.slices.{i+1}.emb.weight
            parts = name.split(".")
            emb_idx = int(parts[1])
            suffix = ".".join(parts[2:])
            new_name = f"depformer.slices.{emb_idx + 1}.emb.{suffix}"

        # Depformer output projections
        elif name.startswith("linears."):
            # linears.{i}.weight → depformer.slices.{i}.linear_out.weight
            parts = name.split(".")
            slice_idx = int(parts[1])
            new_name = f"depformer.slices.{slice_idx}.linear_out.weight"

        # Depformer transformer layers (need to split per slice)
        elif name.startswith("depformer.layers."):
            # These are shared across slices in PyTorch but per-slice in MLX
            # depformer.layers.{j}.norm1.alpha → depformer.slices.{i}.transformer.layers.{j}.norm1.weight
            # depformer.layers.{j}.gating.{i}.* → depformer.slices.{i}.transformer.layers.{j}.gating.*
            # depformer.layers.{j}.self_attn.* → split by num_slices

            parts = name.split(".")
            layer_idx = int(parts[2])
            rest = ".".join(parts[3:])

            if "gating" in name:
                # gating.{slice_idx}.linear_in/out.weight
                gating_parts = rest.split(".")
                slice_idx = int(gating_parts[1])
                gating_rest = ".".join(gating_parts[2:])
                new_name = f"depformer.slices.{slice_idx}.transformer.layers.{layer_idx}.gating.{gating_rest}"

            elif "self_attn" in name:
                # self_attn weights are concatenated, need to split
                # in_proj_weight: [num_slices * 3 * head_dim, dim] → split into num_slices chunks
                # out_proj.weight: [num_slices * dim, head_dim] → split into num_slices chunks
                attn_parts = rest.split(".")
                attn_name = attn_parts[1] if len(attn_parts) > 1 else attn_parts[0]

                for slice_idx in range(num_depformer_slices):
                    chunk = mx.split(tensor, num_depformer_slices, axis=0)[slice_idx]
                    if "in_proj_weight" in name:
                        out_name = f"depformer.slices.{slice_idx}.transformer.layers.{layer_idx}.self_attn.in_proj.weight"
                    else:
                        out_name = f"depformer.slices.{slice_idx}.transformer.layers.{layer_idx}.self_attn.{attn_name}"
                    mlx_weights[out_name] = chunk
                continue  # Already added all slices

            elif "norm1.alpha" in name or "norm2.alpha" in name:
                # Norms are shared, replicate to all slices
                norm_type = "norm1" if "norm1" in name else "norm2"
                squeezed = tensor[0, 0] if tensor.ndim == 3 else (tensor[0] if tensor.ndim == 2 else tensor)
                for slice_idx in range(num_depformer_slices):
                    out_name = f"depformer.slices.{slice_idx}.transformer.layers.{layer_idx}.{norm_type}.weight"
                    mlx_weights[out_name] = squeezed
                continue  # Already added all slices
            else:
                # Other depformer layer weights
                new_name = None  # Skip unknown

        if new_name is not None:
            mlx_weights[new_name] = new_tensor

    return mlx_weights


def save_mlx_weights(weights: dict[str, mx.array], output_path: str):
    """Save weights in MLX-compatible safetensors format."""
    mx.save_safetensors(output_path, weights)
    print(f"Saved {len(weights)} tensors to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Convert PersonaPlex weights to MLX format")
    parser.add_argument(
        "--input",
        type=str,
        default="weights/personaplex/model.safetensors",
        help="Path to PersonaPlex safetensors file",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="weights/personaplex/model_mlx.safetensors",
        help="Output path for MLX weights",
    )
    parser.add_argument(
        "--expand-depformer",
        action="store_true",
        default=True,
        help="Expand depformer from 8 to 16 slices",
    )
    args = parser.parse_args()

    print(f"Loading weights from {args.input}...")
    state_dict = load_safetensors(args.input)
    print(f"Loaded {len(state_dict)} tensors")

    # Show some example keys
    print("\nSample input keys:")
    for i, key in enumerate(sorted(state_dict.keys())[:10]):
        print(f"  {key}: {state_dict[key].shape}")

    if args.expand_depformer:
        print("\nExpanding depformer weights (8 → 16 slices)...")
        state_dict = expand_depformer_weights(state_dict)
        print(f"Now have {len(state_dict)} tensors")

    print("\nRemapping to MLX naming convention...")
    mlx_weights = remap_to_mlx(state_dict)
    print(f"Converted to {len(mlx_weights)} MLX tensors")

    # Show some example output keys
    print("\nSample output keys:")
    for i, key in enumerate(sorted(mlx_weights.keys())[:10]):
        print(f"  {key}: {mlx_weights[key].shape}")

    print(f"\nSaving to {args.output}...")
    save_mlx_weights(mlx_weights, args.output)
    print("Done!")


if __name__ == "__main__":
    main()
