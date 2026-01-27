"""PersonaPlex configuration for MLX."""

from dataclasses import dataclass
from moshi_mlx.models.lm import LmConfig, DepFormerConfig
from moshi_mlx.modules.transformer import TransformerConfig


@dataclass
class PersonaPlexConfig(LmConfig):
    """Configuration for PersonaPlex 7B model."""

    # Voice prompt settings
    voice_prompt_frame_rate: float = 12.5
    voice_prompt_sample_rate: int = 24000
    audio_silence_frame_cnt: int = 6  # 0.5s at 12.5 Hz

    # Silence/sine tokens for conditioning
    silence_tokens: tuple = (948, 243, 1178, 546, 1736, 1030, 1978, 2008)
    sine_tokens: tuple = (430, 1268, 381, 1611, 1095, 1495, 56, 472)

    # Text padding
    zero_text_code: int = 3

    @classmethod
    def personaplex_7b(cls) -> "PersonaPlexConfig":
        """Create PersonaPlex 7B configuration."""
        transformer = TransformerConfig(
            d_model=4096,
            num_heads=32,
            num_layers=32,
            dim_feedforward=int(4.125 * 4096),  # hidden_scale = 4.125
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
            num_slices=16,  # PersonaPlex uses 16 (expanded from 8)
            weights_per_step_schedule=list(range(8)) + list(range(8)),  # 0-7, 0-7
        )

        return cls(
            transformer=transformer,
            depformer=depformer,
            text_in_vocab_size=32001,  # 32000 + padding
            text_out_vocab_size=32000,
            audio_vocab_size=2049,  # 2048 + 1
            audio_codebooks=16,
            audio_delays=[0, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1],  # Without text delay
            conditioners={},
            demux_second_stream=False,
            extra_heads_num_heads=0,
            extra_heads_dim=6,
        )


def get_config() -> PersonaPlexConfig:
    """Get default PersonaPlex 7B configuration."""
    return PersonaPlexConfig.personaplex_7b()
