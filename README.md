# NoX Voice

A personal, self-hosted voice workspace: a webapp in your homelab and NVIDIA
PersonaPlex 7B running natively with MLX on your Apple Silicon Mac.

The interface has light/dark themes, a Settings drawer, and a procedural 3D sphere
that responds **only to the assistant's playback audio**. Microphone audio has no
visualization. All interface text, commands and technical events are in English.

```text
Browser → localhost or LAN HTTPS → webapp + gateway (Docker)
                                      ↓ mutual TLS on the private LAN
                              controller (native Python on Mac)
                                      ↓ authenticated loopback
                              PersonaPlex MLX engine
```

Google login authorizes your browser. A separate client certificate authorizes
the gateway to control the Mac. Only one conversation runs at a time.

The default Compose deployment opens at `http://localhost:8080` on the Docker
host. Google login remains required. Cloudflare Tunnel is optional; neither a
tunnel nor a reverse proxy is needed for localhost. Direct access from another
LAN device requires browser-trusted HTTPS for microphone access.

Local access stays available when a domain is configured. Set the host port once
with `NOX_VOICE_WEB_PORT`; optionally set `NOX_VOICE_PUBLIC_URL` to your HTTPS
domain. Both work simultaneously, with a separate login/session on each origin.
Register both callback URLs in Google; there is no need to switch `.env` values
when moving between local access and the domain.

## Components

| Directory | Responsibility |
| --- | --- |
| `frontend/` | Vite/TypeScript UI, assistant-only Three.js sphere and browser audio |
| `gateway/` | Google OAuth, revocable server sessions, CSRF and streaming proxy |
| `mac/controller/` | Manual/optional automatic startup, mTLS API and engine lifecycle |
| `mac/engine/` | Original PersonaPlex MLX inference, isolated in a worker process |
| `mac/weights/` | Model and voice files; kept on the Mac, never in the webapp image |
| `vendor/personaplex/` | Pinned NVIDIA reference submodule; not a production service |
| `deploy/` | Dockerfile, Compose and deployment configuration |
| `tests/` | Security, lifecycle, asset validation and audio tests |

## First installation

**Follow [Start here: Mac → image → homelab → browser](docs/getting-started.md).**
It is the single, ordered first-install guide, with commands labeled by machine
and a checkpoint after each stage. Start on the Mac Studio. The frontend comes
inside the Docker image; you do not install it separately or run npm for normal
use. The other documents provide reference details after the first installation.

The current port defaults are **8443** for the new HTTPS controller, **8000** for
the private loopback worker, and **8080** inside the webapp container. The Mac
ports are configurable during setup. Only the controller's private address is
configured in the webapp; the browser never connects directly to the Mac.

The public webapp image is intended to be published by the included workflow:

```text
ghcr.io/mapherez/nox-voice-mlx-webapp:latest
```

An image exists only after the workflow has successfully run on GitHub. The first
GHCR package must be made public by its owner. No model files or deployment
credentials are included in that image.

## Model files

Use **only** [nvidia/personaplex-7b-v1](https://huggingface.co/nvidia/personaplex-7b-v1).
The preparation command downloads the necessary files, pins the official commit,
checks hashes, extracts the 18 known voices safely and creates an offline manifest:

```text
mac/weights/
├── model.safetensors
├── tokenizer-e351c8d8-checkpoint125.safetensors
├── tokenizer_spm_32k_3.model
├── voices.tgz
├── manifest.json
└── voices_cpu/
    ├── NATF0.pt
    └── ... 18 known voice prompts
```

Expect about 17 GB of downloads. The Hugging Face account must have accepted the
model's access terms. The engine runs offline after preparation; starting a
conversation does not download code or call a cloud inference API.

PersonaPlex is an English model. Portuguese can be requested through the persona
and evaluated experimentally; this project does not promise Portuguese quality
or silently route speech through a translation service.

## Development and validation

Use native Python 3.12 and Node 24. The root Python environment installs only the
lightweight components and test tools, not the 7B model or its inference stack:

Run these commands from the root of your repository checkout, wherever you
cloned it:

```sh
uv sync --locked
uv run pytest -q
uv run ruff check .
cd frontend
npm ci
npm test
npm run build
```

The mutual TLS test opens an ephemeral loopback listener. Native inference needs
the additional Mac engine dependencies and prepared weights; see the Mac guide.
The archived [original port README](docs/original-port.md) describes the upstream
starting point and is not the installation guide for this version.

## Privacy and operational limits

- NoX Voice does not persist microphone audio, generated audio or transcripts.
  Voice/persona settings are stored privately on the Mac; browser sessions are
  stored in the gateway's persistent volume. Technical events are bounded and
  exclude prompts, audio and credentials.
- With the Cloudflare Tunnel route, audio passes through Cloudflare's network.
  HTTPS terminates there; this is not browser-to-Mac end-to-end encryption.
- The Mac must be powered on, awake and running the controller. Engine controls
  do not wake, reboot or shut down the Mac.
- Leaving the page, backgrounding it on mobile, signing out or losing the stream
  stops microphone capture. Resuming always requires a new user action.
- Stop/restart ends the current conversation. Voice and quantization changes
  require ending it first. There is no model upload or arbitrary command API.

See [architecture and security](docs/architecture.md) and the
[runtime acceptance checklist](docs/validation.md).

## License

The MLX port and new application code retain the repository's MIT license.
PersonaPlex model weights have their own NVIDIA model license; reference code
retains its bundled licenses. Inter is redistributed by the frontend dependency
under the SIL Open Font License. See [third-party notices](docs/notices.md).
