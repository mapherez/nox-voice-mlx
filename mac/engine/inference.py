#!/usr/bin/env python3
"""
PersonaPlex MLX - Live Streaming WebSocket Server with Voice Conditioning

Real-time full-duplex voice conversation on Apple Silicon using MLX.
Includes voice conditioning support for proper response generation.
"""

import copy
import json
import os
from pathlib import Path

import mlx.core as mx
import numpy as np
import rustymimi
from controller.config import VoiceSettings

from .voices import load_embeddings


def report_phase(phase: str):
    print(json.dumps({"phase": phase}), flush=True)


# Constants
SAMPLE_RATE = 24000
FRAME_RATE = 12.5
FRAME_SIZE = int(SAMPLE_RATE / FRAME_RATE)  # 1920 samples per frame

SILENCE_TOKENS = np.array([948, 243, 1178, 546, 1736, 1030, 1978, 2008], dtype=np.int64)
SINE_TOKENS = np.array([430, 1268, 381, 1611, 1095, 1495, 56, 472], dtype=np.int64)
ZERO_TEXT_CODE = 3

WEIGHTS_DIR = Path(os.environ["NOX_VOICE_WEIGHTS_DIR"])
VOICES_DIR = WEIGHTS_DIR / "voices_cpu"

# Global model state
MODEL = None
MIMI = None
CONFIG = None
VOICE_CACHE_STATE = None  # Saved KV cache after voice prompt

# Available voices
VOICES = {
    "NATF0": "Natural Female 1 (Kendra)",
    "NATF1": "Natural Female 2",
    "NATF2": "Natural Female 3",
    "NATF3": "Natural Female 4",
    "NATM0": "Natural Male 1",
    "NATM1": "Natural Male 2",
    "NATM2": "Natural Male 3",
    "NATM3": "Natural Male 4",
    "VARF0": "Variety Female 1",
    "VARF1": "Variety Female 2",
    "VARF2": "Variety Female 3",
    "VARF3": "Variety Female 4",
    "VARF4": "Variety Female 5",
    "VARM0": "Variety Male 1",
    "VARM1": "Variety Male 2",
    "VARM2": "Variety Male 3",
    "VARM3": "Variety Male 4",
    "VARM4": "Variety Male 5",
}


def get_config():
    """Get PersonaPlex 7B configuration."""
    from moshi_mlx.models.lm import DepFormerConfig, LmConfig
    from moshi_mlx.modules.transformer import TransformerConfig

    transformer = TransformerConfig(
        d_model=4096,
        num_heads=32,
        num_layers=32,
        dim_feedforward=int(4.125 * 4096),
        causal=True,
        norm_first=True,
        bias_ff=False,
        bias_attn=False,
        layer_scale=None,
        context=3000,
        max_period=10000,
        use_conv_block=False,
        use_conv_bias=True,
        cross_attention=False,
        gating=True,
        norm="rms_norm",
        positional_embedding="rope",
        conv_layout=False,
        conv_kernel_size=3,
        kv_repeat=1,
        max_seq_len=4096,
    )
    depformer = DepFormerConfig(
        transformer=TransformerConfig(
            d_model=1024,
            num_heads=16,
            num_layers=6,
            dim_feedforward=int(4.125 * 1024),
            causal=True,
            norm_first=True,
            bias_ff=False,
            bias_attn=False,
            layer_scale=None,
            context=8,
            max_period=10000,
            use_conv_block=False,
            use_conv_bias=True,
            cross_attention=False,
            gating=True,
            norm="rms_norm",
            positional_embedding="none",
            conv_layout=False,
            conv_kernel_size=3,
            kv_repeat=1,
            max_seq_len=4096,
        ),
        num_slices=16,
        weights_per_step_schedule=None,
    )
    return LmConfig(
        transformer=transformer,
        depformer=depformer,
        text_in_vocab_size=32001,
        text_out_vocab_size=32000,
        audio_vocab_size=2049,
        audio_codebooks=16,
        audio_delays=[0, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1],
        conditioners={},
        demux_second_stream=False,
    )


def save_cache_state():
    """Save current KV cache state."""
    global VOICE_CACHE_STATE

    state = {"transformer": [], "depformer": []}

    # Save transformer cache
    for layer_cache in MODEL.transformer_cache:
        kv_cache = layer_cache.self_attn
        layer_state = {
            "keys": copy.deepcopy(kv_cache.keys) if kv_cache.keys is not None else None,
            "values": copy.deepcopy(kv_cache.values) if kv_cache.values is not None else None,
            "offset": kv_cache.offset,
            "_idx": kv_cache._idx if hasattr(kv_cache, "_idx") else 0,
        }
        state["transformer"].append(layer_state)

    # Save depformer cache (usually not needed but just in case)
    for layer_cache in MODEL.depformer_cache:
        kv_cache = layer_cache.self_attn
        layer_state = {
            "keys": copy.deepcopy(kv_cache.keys) if kv_cache.keys is not None else None,
            "values": copy.deepcopy(kv_cache.values) if kv_cache.values is not None else None,
            "offset": kv_cache.offset,
        }
        state["depformer"].append(layer_state)

    VOICE_CACHE_STATE = state
    mx.eval(VOICE_CACHE_STATE)
    print(f"Saved cache state (offset={state['transformer'][0]['offset']})")


def restore_cache_state():
    """Restore KV cache state from saved voice prompt state."""
    global VOICE_CACHE_STATE

    if VOICE_CACHE_STATE is None:
        print("No saved cache state to restore!")
        return

    # Restore transformer cache
    for i, layer_cache in enumerate(MODEL.transformer_cache):
        kv_cache = layer_cache.self_attn
        saved = VOICE_CACHE_STATE["transformer"][i]
        if saved["keys"] is not None:
            kv_cache.keys = copy.deepcopy(saved["keys"])
            kv_cache.values = copy.deepcopy(saved["values"])
            kv_cache.offset = saved["offset"]
            if hasattr(kv_cache, "_idx"):
                kv_cache._idx = saved["_idx"]

    # Depformer cache is reset between tokens anyway
    for layer_cache in MODEL.depformer_cache:
        layer_cache.self_attn.reset()


def forward_embeddings(embeddings: mx.array):
    """
    Forward pass through transformer using pre-computed embeddings.

    This is used for replaying voice prompt embeddings.
    """
    transformer_out = MODEL.transformer(embeddings, cache=MODEL.transformer_cache)
    transformer_out = MODEL.out_norm(transformer_out)
    text_logits = MODEL.text_linear(transformer_out)
    mx.eval(transformer_out)  # Force evaluation to update cache
    return transformer_out, text_logits


def step_embeddings(embedding: mx.array, text_sampler, audio_sampler):
    """
    Step through one embedding from voice prompt.

    Processes the embedding through the transformer and samples tokens
    to keep the depformer cache consistent.
    """
    # Forward through transformer
    transformer_out, text_logits = forward_embeddings(embedding)

    # Sample text token
    text_token, _ = text_sampler(text_logits)

    # Sample audio tokens through depformer
    for c in MODEL.depformer_cache:
        c.reset()

    audio_tokens = MODEL.depformer.sample(
        transformer_out,
        audio_sampler,
        text_token,
        MODEL.depformer_cache,
    )

    return text_token, audio_tokens


def step_silence(text_sampler, audio_sampler, num_frames=1):
    """Step through silence frames."""
    silence = mx.array(SILENCE_TOKENS.reshape(1, 8, 1))
    sine = mx.array(SINE_TOKENS.reshape(1, 8, 1))
    text_token = mx.array([[ZERO_TEXT_CODE]])

    # Full 16 codebooks: 8 silence (moshi) + 8 sine (user)
    audio_tokens = []
    for cb in range(8):
        audio_tokens.append(silence[:, cb, :])
    for cb in range(8):
        audio_tokens.append(sine[:, cb, :])

    for _ in range(num_frames):
        text_token, _ = MODEL.sample(text_token, audio_tokens, text_sampler, audio_sampler)
        mx.eval(text_token)


def step_text_prompt(text_tokens: list[int], text_sampler, audio_sampler):
    """Step through text prompt tokens."""
    silence = mx.array(SILENCE_TOKENS.reshape(1, 8, 1))
    sine = mx.array(SINE_TOKENS.reshape(1, 8, 1))

    # Full 16 codebooks: 8 silence (moshi) + 8 sine (user)
    audio_tokens = []
    for cb in range(8):
        audio_tokens.append(silence[:, cb, :])
    for cb in range(8):
        audio_tokens.append(sine[:, cb, :])

    text_token = mx.array([[ZERO_TEXT_CODE]])

    for token_id in text_tokens:
        text_token = mx.array([[token_id]])
        text_token, _ = MODEL.sample(text_token, audio_tokens, text_sampler, audio_sampler)
        mx.eval(text_token)


def load_voice_prompt(voice_name: str, text_prompt: str):
    """
    Load and process voice prompt.

    This replays the voice prompt embeddings through the transformer,
    then processes text prompt, setting up the KV cache with voice characteristics.
    """
    global VOICE_CACHE_STATE

    import sentencepiece
    from moshi_mlx.utils import sampling

    # Reset all caches first
    for c in MODEL.transformer_cache:
        c.reset()
    for c in MODEL.depformer_cache:
        c.reset()

    text_sampler = sampling.Sampler(temp=0.7, top_k=25)
    audio_sampler = sampling.Sampler(temp=0.8, top_k=250)

    # Load voice prompt embeddings
    voice_path = VOICES_DIR / f"{voice_name}.pt"
    print(f"Loading voice prompt: {voice_path}")

    # Only plain tensors are accepted. Never fall back to unrestricted pickle.
    embeddings = load_embeddings(voice_path)  # [frames, 1, 1, 4096]

    # Replay voice prompt embeddings
    print(f"Replaying {embeddings.shape[0]} voice prompt frames...", flush=True)
    for i, emb in enumerate(embeddings):
        # emb shape: [1, 1, 4096]
        emb_mx = mx.array(emb)
        text_token, audio_tokens = step_embeddings(emb_mx, text_sampler, audio_sampler)
        mx.eval(text_token)
        if audio_tokens is not None:
            mx.eval(audio_tokens)

    # Check cache offset after voice prompt
    cache_offset = MODEL.transformer_cache[0].self_attn.offset
    print(f"Done loading voice prompt. Cache offset: {cache_offset}", flush=True)

    # Step through silence
    print("Loading audio silence...", flush=True)
    try:
        step_silence(text_sampler, audio_sampler, num_frames=1)
        print("Done loading audio silence.", flush=True)
    except Exception as e:
        print(f"Error in step_silence: {e}", flush=True)
        import traceback

        traceback.print_exc()

    # Process text prompt
    print("Loading text prompt...", flush=True)
    try:
        tokenizer = sentencepiece.SentencePieceProcessor(
            str(WEIGHTS_DIR / "tokenizer_spm_32k_3.model")
        )
        wrapped_prompt = f"[INST] {text_prompt} [/INST]"
        text_tokens = tokenizer.encode(wrapped_prompt)
        step_text_prompt(text_tokens, text_sampler, audio_sampler)
        print("Done loading text prompt.", flush=True)
    except Exception as e:
        print(f"Error in step_text_prompt: {e}", flush=True)
        import traceback

        traceback.print_exc()

    # Final silence
    print("Loading final audio silence...", flush=True)
    try:
        step_silence(text_sampler, audio_sampler, num_frames=1)
        print("Done loading final audio silence.", flush=True)
    except Exception as e:
        print(f"Error in final step_silence: {e}", flush=True)
        import traceback

        traceback.print_exc()

    # Check final cache state
    final_offset = MODEL.transformer_cache[0].self_attn.offset
    print(f"Final cache offset: {final_offset}", flush=True)

    # Save cache state for restoration on new connections
    save_cache_state()
    print("Voice conditioning complete!", flush=True)


def load_model(settings: VoiceSettings):
    """Load PersonaPlex model and Mimi codec."""
    global MODEL, MIMI, CONFIG

    import mlx.nn as nn
    from moshi_mlx.models.lm import Lm

    report_phase("loading")
    CONFIG = get_config()
    MODEL = Lm(CONFIG)
    MODEL.load_pytorch_weights(str(WEIGHTS_DIR / "model.safetensors"), CONFIG, strict=False)

    report_phase("quantizing")
    if settings.bits:
        nn.quantize(MODEL, group_size=64, bits=settings.bits)
    mx.eval(MODEL.parameters())

    report_phase("codec")
    MIMI = rustymimi.Tokenizer(
        str(WEIGHTS_DIR / "tokenizer-e351c8d8-checkpoint125.safetensors"), num_codebooks=8
    )

    report_phase("warming")
    MODEL.warmup()

    # Load default voice prompt
    report_phase("voice")
    load_voice_prompt(settings.voice, settings.prompt)

    report_phase("ready")
