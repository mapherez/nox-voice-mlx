"""PersonaPlex model wrapper for MLX."""

from pathlib import Path
from typing import Optional
import mlx.core as mx
import mlx.nn as nn

from moshi_mlx.models.lm import Lm, DepFormer, DepFormerSlice, ScaledEmbedding
from moshi_mlx.modules.transformer import Transformer, LayerCache
from moshi_mlx.utils import sampling

from .config import PersonaPlexConfig, get_config


class PersonaPlexLm(nn.Module):
    """
    PersonaPlex Language Model for MLX.

    Extends moshi-mlx Lm with:
    - Voice prompt conditioning
    - Text prompt conditioning
    - Full-duplex streaming support
    """

    def __init__(self, cfg: PersonaPlexConfig):
        super().__init__()
        self.cfg = cfg

        dim = cfg.transformer.d_model

        # Main transformer
        self.transformer = Transformer(cfg.transformer)

        # Depformer for audio token generation
        self.depformer = DepFormer(cfg)

        # Embeddings
        self.text_emb = ScaledEmbedding(
            cfg.text_in_vocab_size,
            dim,
            demux_second_stream=cfg.demux_second_stream,
        )
        self.audio_embs = [
            ScaledEmbedding(cfg.audio_vocab_size, dim)
            for _ in range(cfg.audio_codebooks)
        ]

        # Output layers
        if cfg.transformer.norm == "layer_norm":
            self.out_norm = nn.LayerNorm(dim, 1e-5)
        elif cfg.transformer.norm == "rms_norm":
            self.out_norm = nn.RMSNorm(dim, 1e-8)
        else:
            raise ValueError(f"Unsupported norm: {cfg.transformer.norm}")

        self.text_linear = nn.Linear(dim, cfg.text_out_vocab_size, bias=False)

        # Caches for streaming
        self.transformer_cache: list[LayerCache] = self.transformer.make_rot_cache()
        if len(self.depformer.slices) > 0:
            self.depformer_cache: list[LayerCache] = self.depformer.slices[
                0
            ].transformer.make_cache()
        else:
            self.depformer_cache = []

        # Voice prompt state
        self._voice_prompt_embeddings: Optional[mx.array] = None
        self._voice_prompt_cache: Optional[mx.array] = None

    @property
    def silence_tokens(self) -> mx.array:
        """Get silence token array."""
        return mx.array(self.cfg.silence_tokens, dtype=mx.int32)

    @property
    def sine_tokens(self) -> mx.array:
        """Get sine wave token array."""
        return mx.array(self.cfg.sine_tokens, dtype=mx.int32)

    def load_voice_prompt(self, path: str):
        """
        Load pre-computed voice prompt embeddings.

        Args:
            path: Path to .pt file with embeddings and cache
        """
        # Load PyTorch state dict
        state = mx.load(path)
        self._voice_prompt_embeddings = state.get("embeddings")
        self._voice_prompt_cache = state.get("cache")

    def embed_tokens(
        self,
        text_token: mx.array,
        audio_tokens: list[mx.array],
    ) -> mx.array:
        """
        Embed text and audio tokens.

        Args:
            text_token: Text token [B, 1]
            audio_tokens: List of audio token arrays [B, 1] for each codebook

        Returns:
            Combined embeddings [B, 1, dim]
        """
        xs = self.text_emb(text_token)
        for tokens, emb in zip(audio_tokens, self.audio_embs):
            xs = xs + emb(tokens).transpose(1, 0, 2)
        return xs

    def forward_text(
        self,
        text_token: mx.array,
        audio_tokens: list[mx.array],
    ) -> tuple[mx.array, mx.array]:
        """
        Forward pass through main transformer.

        Args:
            text_token: Text token [B, 1]
            audio_tokens: Audio tokens per codebook

        Returns:
            (transformer_out, text_logits)
        """
        xs = self.embed_tokens(text_token, audio_tokens)
        transformer_out = self.transformer(xs, cache=self.transformer_cache)
        transformer_out = self.out_norm(transformer_out)
        text_logits = self.text_linear(transformer_out)
        return transformer_out, text_logits

    def sample(
        self,
        text_token: mx.array,
        audio_tokens: list[mx.array],
        text_sampler: sampling.Sampler,
        audio_sampler: sampling.Sampler,
    ) -> tuple[mx.array, mx.array]:
        """
        Sample next text and audio tokens.

        Args:
            text_token: Current text token [B, 1]
            audio_tokens: Current audio tokens per codebook
            text_sampler: Sampler for text tokens
            audio_sampler: Sampler for audio tokens

        Returns:
            (next_text_token, next_audio_tokens)
        """
        transformer_out, text_logits = self.forward_text(text_token, audio_tokens)

        # Sample text token
        next_text, _ = text_sampler(text_logits)

        # Sample audio tokens through depformer
        if len(self.depformer.slices) > 0:
            next_audio = self.depformer.sample(
                transformer_out,
                audio_sampler,
                next_text,
                self.depformer_cache,
            )
        else:
            next_audio = None

        return next_text, next_audio

    def reset_caches(self):
        """Reset all streaming caches."""
        for c in self.transformer_cache:
            c.reset()
        for c in self.depformer_cache:
            c.reset()

    def warmup(self):
        """Warmup the model with dummy input."""
        text, audio = self.sample(
            mx.array([[self.cfg.text_out_vocab_size]]),
            [mx.array([[0]])] * (self.cfg.audio_codebooks - self.cfg.depformer.num_slices),
            text_sampler=sampling.Sampler(),
            audio_sampler=sampling.Sampler(),
        )
        # Force evaluation
        mx.eval(text)
        if audio is not None:
            mx.eval(audio)
        self.reset_caches()


def load_personaplex(
    weights_path: str,
    config: Optional[PersonaPlexConfig] = None,
) -> PersonaPlexLm:
    """
    Load PersonaPlex model from MLX weights.

    Args:
        weights_path: Path to model_mlx.safetensors
        config: Optional config, defaults to PersonaPlex 7B

    Returns:
        Loaded PersonaPlexLm model
    """
    if config is None:
        config = get_config()

    model = PersonaPlexLm(config)

    # Load weights
    weights = mx.load(weights_path)
    model.load_weights(list(weights.items()), strict=False)

    return model
