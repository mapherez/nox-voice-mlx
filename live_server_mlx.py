#!/usr/bin/env python3
"""
PersonaPlex MLX - Live Streaming WebSocket Server with Voice Conditioning

Real-time full-duplex voice conversation on Apple Silicon using MLX.
Includes voice conditioning support for proper response generation.
"""

import asyncio
import json
import time
import copy
from pathlib import Path
from typing import Optional

import numpy as np
import mlx.core as mx
import rustymimi
import torch  # For loading .pt files
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

# Constants
SAMPLE_RATE = 24000
FRAME_RATE = 12.5
FRAME_SIZE = int(SAMPLE_RATE / FRAME_RATE)  # 1920 samples per frame

SILENCE_TOKENS = np.array([948, 243, 1178, 546, 1736, 1030, 1978, 2008], dtype=np.int64)
SINE_TOKENS = np.array([430, 1268, 381, 1611, 1095, 1495, 56, 472], dtype=np.int64)
ZERO_TEXT_CODE = 3

WEIGHTS_DIR = Path("/Users/mcruz/Developer/personaplex-mlx/weights")
VOICES_DIR = WEIGHTS_DIR / "voices_cpu"

# Global model state
MODEL = None
MIMI = None
CONFIG = None
VOICE_CACHE_STATE = None  # Saved KV cache after voice prompt

# Lock for model access
MODEL_LOCK = asyncio.Lock()

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
    from moshi_mlx.models.lm import LmConfig, DepFormerConfig
    from moshi_mlx.modules.transformer import TransformerConfig

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
    return LmConfig(
        transformer=transformer, depformer=depformer,
        text_in_vocab_size=32001, text_out_vocab_size=32000,
        audio_vocab_size=2049, audio_codebooks=16,
        audio_delays=[0, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1],
        conditioners={}, demux_second_stream=False,
    )


def save_cache_state():
    """Save current KV cache state."""
    global VOICE_CACHE_STATE

    state = {
        'transformer': [],
        'depformer': []
    }

    # Save transformer cache
    for layer_cache in MODEL.transformer_cache:
        kv_cache = layer_cache.self_attn
        layer_state = {
            'keys': mx.array(kv_cache.keys) if kv_cache.keys is not None else None,
            'values': mx.array(kv_cache.values) if kv_cache.values is not None else None,
            'offset': kv_cache.offset,
            '_idx': kv_cache._idx if hasattr(kv_cache, '_idx') else 0,
        }
        state['transformer'].append(layer_state)

    # Save depformer cache (usually not needed but just in case)
    for layer_cache in MODEL.depformer_cache:
        kv_cache = layer_cache.self_attn
        layer_state = {
            'keys': mx.array(kv_cache.keys) if kv_cache.keys is not None else None,
            'values': mx.array(kv_cache.values) if kv_cache.values is not None else None,
            'offset': kv_cache.offset,
        }
        state['depformer'].append(layer_state)

    VOICE_CACHE_STATE = state
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
        saved = VOICE_CACHE_STATE['transformer'][i]
        if saved['keys'] is not None:
            kv_cache.keys = mx.array(saved['keys'])
            kv_cache.values = mx.array(saved['values'])
            kv_cache.offset = saved['offset']
            if hasattr(kv_cache, '_idx'):
                kv_cache._idx = saved['_idx']

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
        text_token, _ = MODEL.sample(
            text_token, audio_tokens, text_sampler, audio_sampler
        )
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
        text_token, _ = MODEL.sample(
            text_token, audio_tokens, text_sampler, audio_sampler
        )
        mx.eval(text_token)


def load_voice_prompt(voice_name: str, text_prompt: str):
    """
    Load and process voice prompt.

    This replays the voice prompt embeddings through the transformer,
    then processes text prompt, setting up the KV cache with voice characteristics.
    """
    global VOICE_CACHE_STATE

    from moshi_mlx.utils import sampling
    import sentencepiece

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

    # Load PyTorch state dict
    state = torch.load(voice_path, map_location='cpu', weights_only=False)
    # Convert bfloat16 to float32 for numpy compatibility
    embeddings = state['embeddings'].float().numpy()  # [51, 1, 1, 4096]

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
        tokenizer = sentencepiece.SentencePieceProcessor(str(WEIGHTS_DIR / "tokenizer_spm_32k_3.model"))
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


def load_model():
    """Load PersonaPlex model and Mimi codec."""
    global MODEL, MIMI, CONFIG

    from moshi_mlx.models.lm import Lm
    import mlx.nn as nn

    print("Loading PersonaPlex 7B...")
    CONFIG = get_config()
    MODEL = Lm(CONFIG)
    MODEL.load_pytorch_weights(str(WEIGHTS_DIR / "model.safetensors"), CONFIG, strict=False)

    # 4-bit quantization for ~2x speedup
    print("Quantizing to 4-bit...")
    nn.quantize(MODEL, group_size=64, bits=4)
    mx.eval(MODEL.parameters())

    print("Loading Mimi codec...")
    MIMI = rustymimi.Tokenizer(str(WEIGHTS_DIR / "tokenizer-e351c8d8-checkpoint125.safetensors"), num_codebooks=8)

    print("Warming up...")
    MODEL.warmup()

    # Load default voice prompt
    print("\nLoading voice conditioning (this may take a moment)...")
    load_voice_prompt("NATF0", "You are a helpful AI assistant. Be concise and friendly.")

    print("Ready!")


# FastAPI app
app = FastAPI(title="PersonaPlex MLX Live")


@app.on_event("startup")
async def startup_event():
    """Load models on server startup."""
    load_model()


@app.get("/")
async def get_client():
    return HTMLResponse(content=HTML_CLIENT, status_code=200)


@app.get("/voices")
async def get_voices():
    return VOICES


@app.post("/set-voice")
async def set_voice(voice: str = "NATF0", prompt: str = "You are a helpful AI assistant."):
    """Change voice and persona. Requires re-conditioning."""
    if voice not in VOICES:
        return {"error": f"Unknown voice: {voice}"}

    print(f"Changing voice to {voice} with prompt: {prompt[:50]}...")

    # Re-apply voice conditioning with new settings
    load_voice_prompt(voice, prompt)

    return {"status": "ok", "voice": voice}


@app.post("/set-quant")
async def set_quantization(bits: int = 4):
    """Change model quantization level. Requires model reload."""
    global MODEL
    import mlx.nn as nn
    from moshi_mlx.models.lm import Lm

    if bits not in [0, 4, 5, 6, 8]:
        return {"error": "bits must be 0, 4, 5, 6, or 8"}

    print(f"Reloading model with {bits}-bit quantization..." if bits > 0 else "Reloading model without quantization...")

    # Reload model from scratch
    MODEL = Lm(CONFIG)
    MODEL.load_pytorch_weights(str(WEIGHTS_DIR / "model.safetensors"), CONFIG, strict=False)

    if bits > 0:
        nn.quantize(MODEL, group_size=64, bits=bits)

    mx.eval(MODEL.parameters())
    MODEL.warmup()

    # Re-apply voice conditioning
    load_voice_prompt("NATF0", "You are a helpful AI assistant. Be concise and friendly.")

    return {"status": "ok", "bits": bits}


@app.get("/current-quant")
async def get_current_quant():
    """Get current quantization level."""
    # Check if model has quantized layers
    import mlx.nn as nn
    for name, module in MODEL.named_modules():
        if isinstance(module, nn.QuantizedLinear):
            return {"bits": module.bits}
    return {"bits": 0}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handle real-time audio streaming."""
    await websocket.accept()

    from moshi_mlx.utils import sampling

    # Restore voice cache state for this connection
    restore_cache_state()
    cache_offset = MODEL.transformer_cache[0].self_attn.offset
    print(f"Client connected. Cache restored to offset: {cache_offset}", flush=True)

    text_sampler = sampling.Sampler(temp=0.7, top_k=25)
    audio_sampler = sampling.Sampler(temp=0.8, top_k=250)

    text_token = mx.array([[CONFIG.text_out_vocab_size]])

    # Track Moshi's previous audio output for full-duplex
    # Initialize with silence tokens for first frame
    prev_moshi_audio = mx.array(SILENCE_TOKENS.reshape(1, 8, 1), dtype=mx.int32)

    frame_count = 0
    total_gen_time = 0

    try:
        async with MODEL_LOCK:
            while True:
                # Receive audio frame from client
                data = await websocket.receive_bytes()

                if len(data) < FRAME_SIZE * 4:
                    continue

                # Decode float32 audio
                audio_frame = np.frombuffer(data[:FRAME_SIZE * 4], dtype=np.float32)

                # Encode user's audio with Mimi (8 codebooks)
                user_tokens = MIMI.encode_step(audio_frame[None, None, :])
                user_tokens = mx.array(user_tokens.astype(np.int64))

                # Full-duplex: combine Moshi's previous output + user's current input
                # audio_embs[0:7] = Moshi's audio (previous step)
                # audio_embs[8:15] = User's audio (current step)
                all_audio_input = []
                for cb in range(8):
                    all_audio_input.append(prev_moshi_audio[:, cb, :])
                for cb in range(8):
                    all_audio_input.append(user_tokens[:, cb, :])

                # Generate response
                start = time.time()
                next_text, next_audio = MODEL.sample(
                    text_token, all_audio_input, text_sampler, audio_sampler,
                )
                mx.eval(next_text)
                mx.eval(next_audio)
                gen_time = time.time() - start
                total_gen_time += gen_time

                # Save Moshi's output for next iteration (first 8 codebooks)
                prev_moshi_audio = next_audio[:, :8, :]

                # Decode Moshi's audio tokens to PCM (first 8 codebooks only)
                audio_tokens = np.array(next_audio[:, :8, :]).astype(np.uint32)
                output_pcm = MIMI.decode_step(audio_tokens)
                output_frame = output_pcm[0, 0].astype(np.float32)

                # Send audio frame back
                await websocket.send_bytes(output_frame.tobytes())

                text_token = next_text
                frame_count += 1

                # Send stats every 10 frames
                if frame_count % 10 == 0:
                    avg_latency = (total_gen_time / frame_count) * 1000
                    rtf = (frame_count / FRAME_RATE) / total_gen_time
                    stats = {
                        "type": "stats",
                        "frames": frame_count,
                        "avg_latency_ms": round(avg_latency, 1),
                        "rtf": round(rtf, 1),
                    }
                    await websocket.send_text(json.dumps(stats))

    except WebSocketDisconnect:
        print(f"Client disconnected after {frame_count} frames")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


HTML_CLIENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PersonaPlex MLX Live</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #0a0a0a 100%);
            color: #fff;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 2rem;
        }

        h1 {
            color: #76B900;
            font-size: 2rem;
            margin-bottom: 0.5rem;
        }

        .subtitle {
            color: #888;
            margin-bottom: 1rem;
        }

        .badge {
            background: #76B900;
            color: #000;
            padding: 0.25rem 0.75rem;
            border-radius: 12px;
            font-size: 0.75rem;
            font-weight: bold;
            margin-bottom: 2rem;
        }

        .container {
            max-width: 900px;
            width: 100%;
        }

        .config-panel {
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            display: grid;
            grid-template-columns: 1fr 2fr;
            gap: 1rem;
            align-items: start;
        }

        .config-panel label {
            color: #aaa;
            font-size: 0.9rem;
        }

        select, textarea {
            width: 100%;
            padding: 0.75rem;
            border-radius: 8px;
            border: 1px solid #333;
            background: #1a1a1a;
            color: #fff;
            font-family: inherit;
            font-size: 0.9rem;
        }

        select:focus, textarea:focus {
            outline: none;
            border-color: #76B900;
        }

        textarea {
            resize: vertical;
            min-height: 80px;
        }

        .visualizers {
            display: flex;
            justify-content: center;
            gap: 4rem;
            margin: 2rem 0;
        }

        .visualizer {
            text-align: center;
        }

        .visualizer label {
            display: block;
            margin-bottom: 1rem;
            color: #aaa;
            font-size: 0.9rem;
        }

        canvas {
            border-radius: 50%;
        }

        .controls {
            display: flex;
            justify-content: center;
            gap: 1rem;
            margin: 2rem 0;
        }

        button {
            padding: 1rem 2.5rem;
            font-size: 1rem;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
            font-weight: 500;
        }

        #startBtn {
            background: #76B900;
            color: #000;
        }

        #startBtn:hover {
            background: #8fd400;
            transform: translateY(-2px);
        }

        #startBtn:disabled {
            background: #444;
            color: #888;
            cursor: not-allowed;
            transform: none;
        }

        #stopBtn {
            background: #ff4444;
            color: white;
        }

        #stopBtn:disabled {
            background: #444;
            color: #888;
            cursor: not-allowed;
        }

        #muteBtn {
            background: #555;
            color: white;
        }

        #muteBtn:hover:not(:disabled) {
            background: #666;
        }

        #muteBtn.muted {
            background: #ff6b6b;
            color: white;
        }

        #muteBtn:disabled {
            background: #444;
            color: #888;
            cursor: not-allowed;
        }

        .status {
            text-align: center;
            padding: 1rem;
            border-radius: 8px;
            background: rgba(255,255,255,0.05);
            margin-bottom: 1rem;
            font-size: 1.1rem;
        }

        .status.connected {
            border: 2px solid #76B900;
            color: #76B900;
        }

        .status.disconnected {
            border: 2px solid #666;
            color: #888;
        }

        .stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 1rem;
            margin-top: 2rem;
        }

        .stat-box {
            background: rgba(255,255,255,0.05);
            padding: 1.25rem;
            border-radius: 12px;
            text-align: center;
        }

        .stat-value {
            font-size: 2rem;
            color: #76B900;
            font-weight: bold;
        }

        .stat-label {
            font-size: 0.8rem;
            color: #888;
            margin-top: 0.25rem;
        }

        footer {
            margin-top: auto;
            padding-top: 2rem;
            color: #555;
            font-size: 0.8rem;
            text-align: center;
        }
    </style>
</head>
<body>
    <h1>PersonaPlex MLX</h1>
    <p class="subtitle">Live Voice Conversation on Apple Silicon</p>
    <div class="badge">MLX Backend + Voice Conditioning</div>

    <div class="container">
        <div class="config-panel">
            <label for="voice">Voice:</label>
            <select id="voice">
                <optgroup label="Natural Voices">
                    <option value="NATF0" selected>Natural Female 1 (Kendra)</option>
                    <option value="NATF1">Natural Female 2</option>
                    <option value="NATF2">Natural Female 3</option>
                    <option value="NATF3">Natural Female 4</option>
                    <option value="NATM0">Natural Male 1</option>
                    <option value="NATM1">Natural Male 2</option>
                    <option value="NATM2">Natural Male 3</option>
                    <option value="NATM3">Natural Male 4</option>
                </optgroup>
                <optgroup label="Variety Voices">
                    <option value="VARF0">Variety Female 1</option>
                    <option value="VARF1">Variety Female 2</option>
                    <option value="VARF2">Variety Female 3</option>
                    <option value="VARF3">Variety Female 4</option>
                    <option value="VARF4">Variety Female 5</option>
                    <option value="VARM0">Variety Male 1</option>
                    <option value="VARM1">Variety Male 2</option>
                    <option value="VARM2">Variety Male 3</option>
                    <option value="VARM3">Variety Male 4</option>
                    <option value="VARM4">Variety Male 5</option>
                </optgroup>
            </select>

            <label for="prompt">Persona:</label>
            <textarea id="prompt">You are a helpful AI assistant. Be concise and friendly.</textarea>

            <label></label>
            <button id="applyVoiceBtn" style="padding: 0.5rem 1rem; background: #76B900; color: #000; border: none; border-radius: 6px; cursor: pointer; font-weight: 500;">Apply Voice Settings</button>

            <label for="quant">Quantization:</label>
            <select id="quant">
                <option value="4">4-bit (Fastest)</option>
                <option value="5">5-bit</option>
                <option value="6">6-bit</option>
                <option value="8">8-bit (Balanced)</option>
                <option value="0">None (Best Quality)</option>
            </select>
        </div>

        <div class="status disconnected" id="status">
            Click Start to begin conversation
        </div>

        <div class="visualizers">
            <div class="visualizer">
                <label>Your Voice</label>
                <canvas id="userCanvas" width="180" height="180"></canvas>
            </div>
            <div class="visualizer">
                <label>PersonaPlex</label>
                <canvas id="aiCanvas" width="180" height="180"></canvas>
            </div>
        </div>

        <div class="controls">
            <button id="startBtn">Start Conversation</button>
            <button id="muteBtn" disabled>🎤 Mute</button>
            <button id="stopBtn" disabled>Stop</button>
        </div>

        <div class="stats">
            <div class="stat-box">
                <div class="stat-value" id="latency">--</div>
                <div class="stat-label">Latency (ms)</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="rtf">--</div>
                <div class="stat-label">Realtime Factor</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="frames">0</div>
                <div class="stat-label">Frames</div>
            </div>
        </div>
    </div>

    <footer>
        Model: NVIDIA PersonaPlex 7B | Backend: MLX + rustymimi | Voice Conditioned
    </footer>

    <script>
        const TARGET_SAMPLE_RATE = 24000;
        const FRAME_SIZE = 1920;

        let ws = null;
        let audioContext = null;
        let playbackContext = null;
        let mediaStream = null;
        let userAnalyser = null;
        let aiAnalyser = null;
        let inputBuffer = [];
        let nativeSampleRate = 48000;

        // Audio playback queue
        let nextPlayTime = 0;
        const BUFFER_AHEAD = 0.15;
        let audioQueue = [];
        let isPlaying = false;
        let isMuted = false;

        const startBtn = document.getElementById('startBtn');
        const muteBtn = document.getElementById('muteBtn');
        const stopBtn = document.getElementById('stopBtn');
        const status = document.getElementById('status');
        const voiceSelect = document.getElementById('voice');
        const promptInput = document.getElementById('prompt');
        const applyVoiceBtn = document.getElementById('applyVoiceBtn');
        const userCanvas = document.getElementById('userCanvas');
        const aiCanvas = document.getElementById('aiCanvas');
        const latencyEl = document.getElementById('latency');
        const rtfEl = document.getElementById('rtf');
        const framesEl = document.getElementById('frames');
        const quantSelect = document.getElementById('quant');

        // Handle quantization change
        quantSelect.addEventListener('change', async () => {
            const bits = parseInt(quantSelect.value);
            quantSelect.disabled = true;
            status.textContent = `Reloading model with ${bits > 0 ? bits + '-bit' : 'no'} quantization...`;
            status.className = 'status';

            try {
                const resp = await fetch('/set-quant?bits=' + bits, { method: 'POST' });
                const data = await resp.json();
                if (data.error) {
                    status.textContent = 'Error: ' + data.error;
                } else {
                    status.textContent = `Model reloaded (${bits > 0 ? bits + '-bit' : 'full precision'})`;
                }
            } catch (e) {
                status.textContent = 'Error changing quantization';
            }
            quantSelect.disabled = false;
        });

        // Handle voice/persona change
        applyVoiceBtn.addEventListener('click', async () => {
            const voice = voiceSelect.value;
            const prompt = promptInput.value;
            applyVoiceBtn.disabled = true;
            voiceSelect.disabled = true;
            promptInput.disabled = true;
            status.textContent = `Applying voice: ${voiceSelect.options[voiceSelect.selectedIndex].text}...`;
            status.className = 'status';

            try {
                const resp = await fetch(`/set-voice?voice=${voice}&prompt=${encodeURIComponent(prompt)}`, { method: 'POST' });
                const data = await resp.json();
                if (data.error) {
                    status.textContent = 'Error: ' + data.error;
                } else {
                    status.textContent = `Voice applied: ${voiceSelect.options[voiceSelect.selectedIndex].text}`;
                }
            } catch (e) {
                status.textContent = 'Error changing voice';
            }
            applyVoiceBtn.disabled = false;
            voiceSelect.disabled = false;
            promptInput.disabled = false;
        });

        function resample(input, fromRate, toRate) {
            if (fromRate === toRate) return input;
            const ratio = fromRate / toRate;
            const outputLength = Math.floor(input.length / ratio);
            const output = new Float32Array(outputLength);
            for (let i = 0; i < outputLength; i++) {
                const srcIndex = i * ratio;
                const srcIndexFloor = Math.floor(srcIndex);
                const frac = srcIndex - srcIndexFloor;
                const a = input[srcIndexFloor] || 0;
                const b = input[srcIndexFloor + 1] || a;
                output[i] = a + frac * (b - a);
            }
            return output;
        }

        function drawVisualizer(canvas, analyser, color) {
            const ctx = canvas.getContext('2d');
            const width = canvas.width;
            const height = canvas.height;
            const centerX = width / 2;
            const centerY = height / 2;
            const maxRadius = width * 0.42;

            function draw() {
                requestAnimationFrame(draw);

                let intensity = 0;
                if (analyser) {
                    const dataArray = new Uint8Array(analyser.frequencyBinCount);
                    analyser.getByteFrequencyData(dataArray);
                    intensity = dataArray.reduce((a, b) => a + b, 0) / dataArray.length / 255;
                }

                const gradient = ctx.createRadialGradient(centerX, centerY, 0, centerX, centerY, maxRadius);
                gradient.addColorStop(0, '#1a1a2e');
                gradient.addColorStop(1, '#0a0a0a');
                ctx.fillStyle = gradient;
                ctx.fillRect(0, 0, width, height);

                ctx.beginPath();
                ctx.arc(centerX, centerY, maxRadius, 0, Math.PI * 2);
                ctx.strokeStyle = '#333';
                ctx.lineWidth = 3;
                ctx.stroke();

                const radius = maxRadius * (0.25 + 0.6 * intensity);
                const circleGradient = ctx.createRadialGradient(centerX, centerY, 0, centerX, centerY, radius);
                circleGradient.addColorStop(0, '#9acd32');
                circleGradient.addColorStop(1, color);
                ctx.beginPath();
                ctx.arc(centerX, centerY, radius, 0, Math.PI * 2);
                ctx.fillStyle = circleGradient;
                ctx.fill();

                ctx.beginPath();
                ctx.arc(centerX, centerY, maxRadius * 0.12, 0, Math.PI * 2);
                ctx.fillStyle = '#76B900';
                ctx.fill();
            }
            draw();
        }

        async function start() {
            try {
                status.textContent = 'Connecting...';
                status.className = 'status';

                mediaStream = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        channelCount: 1,
                        echoCancellation: true,
                        noiseSuppression: true,
                    }
                });

                audioContext = new AudioContext();
                nativeSampleRate = audioContext.sampleRate;

                playbackContext = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });

                userAnalyser = audioContext.createAnalyser();
                userAnalyser.fftSize = 256;

                aiAnalyser = playbackContext.createAnalyser();
                aiAnalyser.fftSize = 256;

                const source = audioContext.createMediaStreamSource(mediaStream);
                source.connect(userAnalyser);

                const bufferSize = 4096;
                const scriptProcessor = audioContext.createScriptProcessor(bufferSize, 1, 1);
                source.connect(scriptProcessor);
                // Don't connect to destination - that causes echo!
                // scriptProcessor needs a destination to work, use a silent gain node
                const silentGain = audioContext.createGain();
                silentGain.gain.value = 0;
                scriptProcessor.connect(silentGain);
                silentGain.connect(audioContext.destination);

                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
                ws.binaryType = 'arraybuffer';

                ws.onopen = () => {
                    status.textContent = '● Connected - Speak now!';
                    status.className = 'status connected';
                    startBtn.disabled = true;
                    muteBtn.disabled = false;
                    stopBtn.disabled = false;
                };

                ws.onmessage = (event) => {
                    if (typeof event.data === 'string') {
                        const stats = JSON.parse(event.data);
                        latencyEl.textContent = stats.avg_latency_ms;
                        rtfEl.textContent = stats.rtf + 'x';
                        framesEl.textContent = stats.frames;
                    } else {
                        const audioData = new Float32Array(event.data);
                        playAudio(audioData);
                    }
                };

                ws.onclose = () => {
                    status.textContent = '○ Disconnected';
                    status.className = 'status disconnected';
                    startBtn.disabled = false;
                    muteBtn.disabled = true;
                    muteBtn.classList.remove('muted');
                    muteBtn.textContent = '🎤 Mute';
                    isMuted = false;
                    stopBtn.disabled = true;
                };

                ws.onerror = (err) => {
                    console.error('WebSocket error:', err);
                    status.textContent = '✗ Connection error';
                };

                scriptProcessor.onaudioprocess = (e) => {
                    if (!ws || ws.readyState !== WebSocket.OPEN) return;

                    const inputData = e.inputBuffer.getChannelData(0);
                    const resampled = resample(inputData, nativeSampleRate, TARGET_SAMPLE_RATE);
                    inputBuffer.push(...resampled);

                    while (inputBuffer.length >= FRAME_SIZE) {
                        let frame = new Float32Array(inputBuffer.splice(0, FRAME_SIZE));
                        // Send silence when muted
                        if (isMuted) {
                            frame = new Float32Array(FRAME_SIZE);
                        }
                        ws.send(frame.buffer);
                    }
                };

                drawVisualizer(userCanvas, userAnalyser, '#4E8800');
                drawVisualizer(aiCanvas, aiAnalyser, '#4E8800');

            } catch (err) {
                console.error('Error starting:', err);
                status.textContent = '✗ Error: ' + err.message;
            }
        }

        function playAudio(audioData) {
            if (!playbackContext) return;

            audioQueue.push(audioData);

            if (!isPlaying && audioQueue.length >= 3) {
                isPlaying = true;
                schedulePlayback();
            } else if (isPlaying) {
                schedulePlayback();
            }
        }

        function schedulePlayback() {
            if (!playbackContext || audioQueue.length === 0) return;

            const currentTime = playbackContext.currentTime;

            if (nextPlayTime < currentTime) {
                nextPlayTime = currentTime + BUFFER_AHEAD;
            }

            while (audioQueue.length > 0) {
                const audioData = audioQueue.shift();

                const buffer = playbackContext.createBuffer(1, audioData.length, TARGET_SAMPLE_RATE);
                buffer.getChannelData(0).set(audioData);

                const source = playbackContext.createBufferSource();
                source.buffer = buffer;
                source.connect(aiAnalyser);
                aiAnalyser.connect(playbackContext.destination);
                source.start(nextPlayTime);

                nextPlayTime += buffer.duration;
            }
        }

        function stop() {
            if (ws) {
                ws.close();
                ws = null;
            }
            if (mediaStream) {
                mediaStream.getTracks().forEach(track => track.stop());
                mediaStream = null;
            }
            if (audioContext) {
                audioContext.close();
                audioContext = null;
            }
            if (playbackContext) {
                playbackContext.close();
                playbackContext = null;
            }
            inputBuffer = [];
            audioQueue = [];
            isPlaying = false;
            nextPlayTime = 0;

            status.textContent = '○ Disconnected';
            status.className = 'status disconnected';
            startBtn.disabled = false;
            stopBtn.disabled = true;
        }

        startBtn.addEventListener('click', start);
        muteBtn.addEventListener('click', () => {
            isMuted = !isMuted;
            if (isMuted) {
                muteBtn.textContent = '🔇 Unmute';
                muteBtn.classList.add('muted');
            } else {
                muteBtn.textContent = '🎤 Mute';
                muteBtn.classList.remove('muted');
            }
        });
        stopBtn.addEventListener('click', stop);

        drawVisualizer(userCanvas, null, '#4E8800');
        drawVisualizer(aiCanvas, null, '#4E8800');

        // Load current quant level on page load
        fetch('/current-quant').then(r => r.json()).then(data => {
            quantSelect.value = data.bits.toString();
        });
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    print("=" * 50)
    print("PersonaPlex MLX Live Server (Voice Conditioned)")
    print("=" * 50)
    print("Open http://localhost:8002 in your browser")
    print("=" * 50 + "\n")

    uvicorn.run(app, host="0.0.0.0", port=8000)
