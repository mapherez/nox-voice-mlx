# PersonaPlex MLX

Real-time voice conversation AI running on Apple Silicon. A port of NVIDIA's PersonaPlex 7B to Apple's MLX framework.

![PersonaPlex MLX](https://img.shields.io/badge/MLX-Apple%20Silicon-black?style=flat-square&logo=apple)
![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)

## Features

- **Real-time voice conversation** - Full-duplex audio streaming at 24kHz
- **Voice conditioning** - 18 different voice personas (natural & variety)
- **Customizable personas** - Set system prompts for different AI personalities
- **Dynamic quantization** - Switch between 4/5/6/8-bit on the fly
- **Web UI** - Clean browser interface with audio visualization
- **~1.2x realtime** - Runs faster than realtime on M1/M2/M3 Macs

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    PersonaPlex MLX Pipeline                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Browser (WebSocket)                                             │
│       │                                                          │
│       ▼                                                          │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              live_server_mlx.py                          │    │
│  │  FastAPI + WebSocket server                              │    │
│  └─────────────────────────────────────────────────────────┘    │
│       │                                                          │
│       ▼                                                          │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │ Mimi Encode │───▶│   LM (7B)   │───▶│ Mimi Decode │         │
│  │   ~19ms     │    │   ~26ms     │    │   ~19ms     │         │
│  └─────────────┘    └─────────────┘    └─────────────┘         │
│                            │                                     │
│                            ▼                                     │
│                     ┌─────────────┐                             │
│                     │  DepFormer  │                             │
│                     │  16 slices  │                             │
│                     │   ~12ms     │                             │
│                     └─────────────┘                             │
│                                                                  │
│  Total: ~65ms per 80ms frame = 1.23x realtime                   │
└─────────────────────────────────────────────────────────────────┘
```

## Requirements

- macOS with Apple Silicon (M1/M2/M3)
- Python 3.10+
- ~8GB RAM (4-bit quantization)
- ~16GB disk for weights

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/personaplex-mlx.git
cd personaplex-mlx
```

### 2. Install dependencies with uv

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create venv and install dependencies
uv sync
```

### 3. Download weights

Download the PersonaPlex weights from HuggingFace:

```bash
uv run download-weights
```

Or manually download from [HuggingFace](https://huggingface.co/nvidia/PersonaPlex-7B) and place in `weights/`.

Required files:
- `weights/model.safetensors` (~16GB)
- `weights/tokenizer-e351c8d8-checkpoint125.safetensors` (~385MB)
- `weights/tokenizer_spm_32k_3.model` (~553KB)
- `weights/voices_cpu/*.pt` (voice prompts)

## Usage

### Start the server

```bash
uv run serve
```

Open http://localhost:8000 in your browser.

### Web UI Controls

- **Voice** - Select from 18 voice personas
- **Persona** - Customize the AI's system prompt
- **Quantization** - Switch between 4/5/6/8-bit (or full precision)
- **Start/Stop** - Begin/end conversation
- **Mute** - Mute your microphone

## Scripts

| Command | Description |
|---------|-------------|
| `uv run serve` | Start the web server |
| `uv run download-weights` | Download model weights |
| `uv run test` | Run comparison tests |

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web UI |
| `/ws` | WebSocket | Real-time audio streaming |
| `/voices` | GET | List available voices |
| `/set-voice` | POST | Change voice and persona |
| `/set-quant` | POST | Change quantization level |
| `/current-quant` | GET | Get current quantization |

## Performance

Tested on Apple Silicon:

| Quantization | Model Time | Total Latency | RTF |
|--------------|------------|---------------|-----|
| 4-bit | ~26ms | ~65ms | 1.23x |
| 5-bit | ~30ms | ~70ms | 1.14x |
| 6-bit | ~35ms | ~75ms | 1.07x |
| 8-bit | ~45ms | ~85ms | 0.94x |
| None | ~80ms | ~120ms | 0.67x |

## Project Structure

```
personaplex-mlx/
├── live_server_mlx.py      # Main server (FastAPI + WebSocket)
├── convert_weights.py      # Weight conversion utility
├── test_pytorch_mlx_comparison.py  # Verification tests
├── pyproject.toml          # Dependencies and scripts
├── weights/                # Model weights (not in git)
│   ├── model.safetensors
│   ├── tokenizer*.safetensors
│   └── voices_cpu/*.pt
├── personaplex/            # Original PyTorch reference
└── personaplex_mlx/        # MLX model config
```

## Dependencies

- [MLX](https://github.com/ml-explore/mlx) - Apple's ML framework
- [moshi-mlx](https://github.com/kyutai-labs/moshi) - Base model implementation
- [rustymimi](https://github.com/kyutai-labs/moshi) - Neural audio codec
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework

## References

- [PersonaPlex Paper](https://research.nvidia.com/labs/adlr/files/personaplex/personaplex_preprint.pdf)
- [PersonaPlex Model](https://huggingface.co/nvidia/personaplex-7b-v1)
- [Moshi/Mimi](https://github.com/kyutai-labs/moshi)

## License

MIT License - See [LICENSE](LICENSE) for details.

---

Built with MLX on Apple Silicon
