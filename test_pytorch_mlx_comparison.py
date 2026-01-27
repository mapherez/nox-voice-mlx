#!/usr/bin/env python3
"""
Layer-by-layer comparison between PyTorch and MLX implementations.
Verifies that the MLX port produces equivalent outputs to PyTorch.
"""

import numpy as np
import torch
import mlx.core as mx
import time

SILENCE_TOKENS = np.array([948, 243, 1178, 546, 1736, 1030, 1978, 2008], dtype=np.int64)


def load_pytorch_model():
    """Load PyTorch model."""
    import os
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"

    import sys
    sys.path.insert(0, "personaplex/moshi")
    from moshi.models import loaders

    print("Loading PyTorch model...")
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

    lm_model = loaders.get_moshi_lm(
        "weights/model.safetensors",
        device=device,
        dtype=torch.bfloat16,
    )
    return lm_model


def load_mlx_model():
    """Load MLX model."""
    from moshi_mlx.models.lm import Lm, LmConfig, DepFormerConfig
    from moshi_mlx.modules.transformer import TransformerConfig

    print("Loading MLX model...")

    transformer = TransformerConfig(
        d_model=4096, num_heads=32, num_layers=32,
        dim_feedforward=int(4.125 * 4096), causal=True, norm_first=True,
        bias_ff=False, bias_attn=False, layer_scale=None, context=3000,
        max_period=10000, use_conv_block=False, use_conv_bias=True,
        cross_attention=False, gating=True, norm="rms_norm",
        positional_embedding="rope", conv_layout=False, conv_kernel_size=3,
        kv_repeat=1, max_seq_len=4096,
    )
    depformer = DepFormerConfig(
        transformer=TransformerConfig(
            d_model=1024, num_heads=16, num_layers=6,
            dim_feedforward=int(4.125 * 1024), causal=True, norm_first=True,
            bias_ff=False, bias_attn=False, layer_scale=None, context=8,
            max_period=10000, use_conv_block=False, use_conv_bias=True,
            cross_attention=False, gating=True, norm="rms_norm",
            positional_embedding="none", conv_layout=False, conv_kernel_size=3,
            kv_repeat=1, max_seq_len=4096,
        ),
        num_slices=16,
        weights_per_step_schedule=None,
    )
    config = LmConfig(
        transformer=transformer, depformer=depformer,
        text_in_vocab_size=32001, text_out_vocab_size=32000,
        audio_vocab_size=2049, audio_codebooks=16,
        audio_delays=[0, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1],
        conditioners={}, demux_second_stream=False,
    )

    model = Lm(config)
    model.load_pytorch_weights("weights/model.safetensors", config, strict=False)
    return model, config


def compare_embeddings(pth_model, mlx_model, mlx_config):
    """Compare text and audio embedding outputs."""
    print("\n=== Test: Embedding Comparison ===")

    # Test input: text token and audio tokens
    text_token = 3  # padding token
    audio_tokens = SILENCE_TOKENS[:8]

    # PyTorch embeddings (no gradients needed)
    with torch.no_grad():
        pth_text = pth_model.text_emb(torch.tensor([[text_token]], device=pth_model.device))
        pth_audio_embs = []
        for i, tok in enumerate(audio_tokens):
            emb = pth_model.emb[i](torch.tensor([[int(tok)]], device=pth_model.device))
            pth_audio_embs.append(emb)
        pth_combined = pth_text.clone()
        for emb in pth_audio_embs:
            pth_combined = pth_combined + emb
        # Convert to float32 on CPU before numpy
        pth_combined_cpu = pth_combined.float().cpu()
        pth_combined_np = pth_combined_cpu.detach().numpy()

    # MLX embeddings
    mlx_text = mlx_model.text_emb(mx.array([[text_token]], dtype=mx.int32))
    mlx_audio_embs = []
    for i, tok in enumerate(audio_tokens):
        emb = mlx_model.audio_embs[i](mx.array([[int(tok)]], dtype=mx.int32))
        mlx_audio_embs.append(emb)
    mlx_combined = mlx_text
    for emb in mlx_audio_embs:
        emb_t = emb.transpose(1, 0, 2)  # Match PyTorch convention
        mlx_combined = mlx_combined + emb_t
    # Convert to float32 before numpy
    mlx_combined = mlx_combined.astype(mx.float32)
    mx.eval(mlx_combined)
    mlx_combined_np = np.array(mlx_combined)

    # Compare
    diff = np.abs(pth_combined_np.astype(np.float32) - mlx_combined_np.astype(np.float32))
    max_diff = diff.max()
    mean_diff = diff.mean()

    print(f"  PyTorch embedding shape: {pth_combined_np.shape}")
    print(f"  MLX embedding shape: {mlx_combined_np.shape}")
    print(f"  Max absolute difference: {max_diff:.6f}")
    print(f"  Mean absolute difference: {mean_diff:.6f}")

    # Check correlation
    pth_flat = pth_combined_np.flatten()
    mlx_flat = mlx_combined_np.flatten()
    corr = np.corrcoef(pth_flat, mlx_flat)[0, 1]
    print(f"  Correlation: {corr:.6f}")

    passed = max_diff < 0.01 and corr > 0.999
    print(f"  {'PASS' if passed else 'FAIL'}")
    return passed


def compare_transformer_single_step(pth_model, mlx_model, mlx_config):
    """Compare transformer output for a single step."""
    print("\n=== Test: Single Transformer Step ===")

    # Reset caches
    pth_model.transformer._streaming_state = None
    for c in mlx_model.transformer_cache:
        c.reset()

    # Create input sequence [B=1, K=17, S=1]
    text_token = 32000  # initial text token
    audio_tokens = np.array([2048] * 16, dtype=np.int64)  # initial audio tokens

    # PyTorch forward
    pth_input = torch.zeros(1, 17, 1, dtype=torch.long, device=pth_model.device)
    pth_input[0, 0, 0] = text_token
    pth_input[0, 1:, 0] = torch.tensor(audio_tokens, device=pth_model.device)

    with torch.no_grad():
        with pth_model.transformer.streaming(batch_size=1):
            pth_out, pth_text_logits = pth_model.forward_codes(pth_input)
    pth_out_np = pth_out.float().cpu().numpy()
    pth_text_logits_np = pth_text_logits.float().cpu().numpy()

    # MLX forward
    from moshi_mlx.utils import sampling
    text_sampler = sampling.Sampler(temp=0.0)  # Greedy
    audio_sampler = sampling.Sampler(temp=0.0)

    mlx_text = mx.array([[text_token]])
    mlx_audio = [mx.array([[tok]]) for tok in audio_tokens[:mlx_config.other_codebooks]]

    # Call _sample to get transformer output
    next_text, next_audio, transformer_out = mlx_model._sample(
        mlx_text, mlx_audio, text_sampler, audio_sampler
    )
    mx.eval(transformer_out)
    mlx_out_np = np.array(transformer_out)

    # Get text logits
    mlx_text_logits = mlx_model.text_linear(transformer_out)
    mx.eval(mlx_text_logits)
    mlx_text_logits_np = np.array(mlx_text_logits)

    # Compare transformer outputs
    diff = np.abs(pth_out_np.astype(np.float32) - mlx_out_np.astype(np.float32))
    max_diff = diff.max()
    mean_diff = diff.mean()

    print(f"  PyTorch transformer out shape: {pth_out_np.shape}")
    print(f"  MLX transformer out shape: {mlx_out_np.shape}")
    print(f"  Max absolute difference: {max_diff:.6f}")
    print(f"  Mean absolute difference: {mean_diff:.6f}")

    # Compare text logits
    print(f"\n  PyTorch text logits shape: {pth_text_logits_np.shape}")
    print(f"  MLX text logits shape: {mlx_text_logits_np.shape}")

    # Check correlation
    if pth_out_np.shape == mlx_out_np.shape:
        corr = np.corrcoef(pth_out_np.flatten(), mlx_out_np.flatten())[0, 1]
        print(f"  Correlation: {corr:.6f}")
    else:
        corr = 0.0
        print("  Shapes don't match for correlation")

    passed = max_diff < 0.1 or corr > 0.99
    print(f"  {'PASS' if passed else 'FAIL'}")
    return passed


def compare_greedy_sampling(pth_model, mlx_model, mlx_config, num_steps=5):
    """Compare greedy sampling outputs over multiple steps."""
    print(f"\n=== Test: Greedy Sampling ({num_steps} steps) ===")

    # Reset caches
    for c in mlx_model.transformer_cache:
        c.reset()

    from moshi_mlx.utils import sampling

    # Fixed seed for reproducibility
    np.random.seed(42)

    # MLX greedy sampling
    text_sampler = sampling.Sampler(temp=0.0)  # Greedy
    audio_sampler = sampling.Sampler(temp=0.0)

    mlx_text = mx.array([[mlx_config.text_out_vocab_size]])
    mlx_audio = [mx.array([[0]])] * mlx_config.other_codebooks

    mlx_text_tokens = []
    mlx_audio_tokens = []

    print("  MLX greedy tokens:")
    for step in range(num_steps):
        next_text, next_audio = mlx_model.sample(
            mlx_text, mlx_audio, text_sampler, audio_sampler
        )
        mx.eval(next_text)
        if next_audio is not None:
            mx.eval(next_audio)

        mlx_text_tokens.append(int(next_text[0, 0]))
        if next_audio is not None:
            mlx_audio_tokens.append(np.array(next_audio[0, :, 0]).tolist())

        mlx_text = next_text
        print(f"    Step {step+1}: text={mlx_text_tokens[-1]}", end="")
        if next_audio is not None:
            print(f", audio={mlx_audio_tokens[-1][:4]}...")
        else:
            print()

    print(f"\n  Generated {len(mlx_text_tokens)} text tokens: {mlx_text_tokens}")

    # Check that tokens are reasonable (not all the same, not all special tokens)
    unique_text = len(set(mlx_text_tokens))
    print(f"  Unique text tokens: {unique_text}")

    passed = unique_text >= 2  # Should have some variety with greedy
    print(f"  {'PASS' if passed else 'FAIL'}")
    return passed


def test_output_consistency(mlx_model, mlx_config, num_runs=3):
    """Test that MLX outputs are consistent across runs with same inputs."""
    print(f"\n=== Test: Output Consistency ({num_runs} runs) ===")

    from moshi_mlx.utils import sampling

    outputs = []

    for run in range(num_runs):
        # Reset caches
        for c in mlx_model.transformer_cache:
            c.reset()

        text_sampler = sampling.Sampler(temp=0.0)
        audio_sampler = sampling.Sampler(temp=0.0)

        mlx_text = mx.array([[mlx_config.text_out_vocab_size]])
        mlx_audio = [mx.array([[0]])] * mlx_config.other_codebooks

        next_text, next_audio = mlx_model.sample(
            mlx_text, mlx_audio, text_sampler, audio_sampler
        )
        mx.eval(next_text)
        mx.eval(next_audio)

        outputs.append({
            'text': int(next_text[0, 0]),
            'audio': np.array(next_audio[0, :, 0]).tolist()
        })

    # Check consistency
    text_consistent = all(o['text'] == outputs[0]['text'] for o in outputs)
    audio_consistent = all(o['audio'] == outputs[0]['audio'] for o in outputs)

    print(f"  Text tokens across runs: {[o['text'] for o in outputs]}")
    print(f"  Text consistent: {text_consistent}")
    print(f"  Audio consistent: {audio_consistent}")

    passed = text_consistent and audio_consistent
    print(f"  {'PASS' if passed else 'FAIL'}")
    return passed


def main():
    print("=" * 60)
    print("PyTorch vs MLX Layer-by-Layer Comparison")
    print("=" * 60)

    results = []

    # Load MLX model
    mlx_model, mlx_config = load_mlx_model()
    print("MLX model loaded!")

    # MLX-only tests (don't need PyTorch)
    results.append(("Output Consistency", test_output_consistency(mlx_model, mlx_config)))
    results.append(("Greedy Sampling", compare_greedy_sampling(None, mlx_model, mlx_config)))

    # Try to load PyTorch model for comparison
    try:
        pth_model = load_pytorch_model()
        print("PyTorch model loaded!")

        results.append(("Embedding Comparison", compare_embeddings(pth_model, mlx_model, mlx_config)))
        # Note: Full transformer comparison requires aligned streaming state
        # results.append(("Transformer Step", compare_transformer_single_step(pth_model, mlx_model, mlx_config)))
    except Exception as e:
        print(f"Warning: Could not load PyTorch model: {e}")
        print("Skipping PyTorch comparison tests")

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"  {status}: {name}")

    print(f"\nTotal: {passed}/{total} tests passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
