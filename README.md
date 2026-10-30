# NoX Voice

A self-hosted voice app with NVIDIA PersonaPlex 7B running natively through MLX
on an Apple Silicon Mac. A Docker webapp serves the interface and connects to
the Mac over the private network.

**[Start with the installation guide](docs/getting-started.md).** It covers the
Mac service, Docker deployment, Google login and the first conversation, in that
order. Each command is labeled with the machine and working directory it needs.

## What runs where

| Component             | Runs on                  | Purpose                                                   |
| --------------------- | ------------------------ | --------------------------------------------------------- |
| Controller and engine | Apple Silicon Mac        | Load the model and process conversations                  |
| Webapp and gateway    | Docker host              | Serve the frontend, authenticate browsers and relay audio |
| Browser               | Desktop or mobile device | Capture the microphone and play the assistant's audio     |

```text
Browser → Docker webapp → private HTTPS/mTLS → Mac controller → local MLX engine
```

The Mac service starts manually. Load and eject the model from the webapp's
Settings workspace; stop the manual controller with Ctrl+C. Automatic startup is an
optional setting described in the [Mac reference](docs/mac-setup.md).

## Access

- **Local:** open `http://localhost:8080` on the Docker host. No tunnel or reverse
  proxy is required. The host port is configurable.
- **From another computer:** an SSH forward can provide the same localhost
  access. Direct LAN access from desktop or mobile browsers uses a trusted HTTPS
  hostname for microphone access.
- **External, optional:** add an HTTPS domain and route it to the webapp. The
  supplied Cloudflare Tunnel profile is one supported option.

Local access remains available when an HTTPS domain is configured. Both can be
used at the same time, with a separate login session on each address. Google
login is required for both and needs Internet access to Google.

## Features and limits

- PersonaPlex 7B with 18 voice prompts, a configurable persona, and
  full/4/5/6/8-bit precision. **7B describes the parameter count**, not bit depth.
- Versioned model profiles and a controller-supplied model catalogue in Settings.
  PersonaPlex keeps its existing conversation flow. LFM3 now provides explicit
  transcription/speech checks in this webapp, with Space/touch interruption and
  independent language/voice settings. Its executor and conversation flow are
  the next implementation stage; the isolated experiment remains separate.
- Download either model from Settings, with per-file/total progress, cancel and
  resume in the Download Manager. Load/Eject controls memory independently of
  downloads. Open each model folder in Finder on the controller Mac.
- A general Credentials section with private Hugging Face token storage and
  access testing. PersonaPlex needs model access and a read token; LFM3 downloads
  directly. No manual model-file downloads are required.
- A light/dark interface, Settings organized into tabs, and a procedural sphere
  that reacts only to assistant playback.
- One active conversation at a time. The app does not persist audio or
  transcripts; it persists settings, credentials, recent download jobs and
  authenticated sessions.
- The Mac must be awake and running its controller. Performance and memory use
  depend on the Mac, model precision and network.
- PersonaPlex officially supports English. LFM3 language controls follow its
  pinned runtime; language and speech quality require evaluation on your device.

The current engine requires Apple Silicon; Intel Macs are not supported. Use
native ARM64 Python 3.12. Normal Docker deployment does not require Node or npm.

## Documentation

| Document                                   | Read it for                                                                       |
| ------------------------------------------ | --------------------------------------------------------------------------------- |
| [Installation](docs/getting-started.md)    | The complete first installation                                                   |
| [Mac reference](docs/mac-setup.md)         | Paths, ports, moving the installation, optional autostart and certificate renewal |
| [Deployment reference](docs/deployment.md) | Environment variables, secrets, LAN/external access, updates and troubleshooting  |
| [Architecture](docs/architecture.md)       | Authentication, transport, model loading and privacy boundaries                   |
| [Multi-model engine contract](docs/voice-engine-contract.md) | Agreed target and implementation progress for PersonaPlex/LFM3, settings, interruption, tasks and NoX Suite integration |
| [Validation](docs/validation.md)           | Development checks and deployment acceptance                                      |
| [Third-party notices](docs/notices.md)     | Licenses and upstream components                                                  |

## Repository layout

| Directory             | Contents                                                       |
| --------------------- | -------------------------------------------------------------- |
| `frontend/`           | TypeScript interface, sphere and browser audio                 |
| `gateway/`            | Authentication, sessions and audio proxy                       |
| `mac/controller/`     | Native controller, certificates and process lifecycle          |
| `mac/engine/`         | PersonaPlex MLX inference worker                               |
| `mac/weights/`        | Separate `personaplex/` and `lfm3-audio/` asset folders; large assets are excluded from Git |
| `deploy/`             | Compose, image build definition and environment template       |
| `tests/`              | Security, lifecycle, streaming and native compatibility checks |
| `vendor/personaplex/` | Pinned NVIDIA reference submodule                              |

The supplied Compose file selects the project's GHCR image. Building and
publishing an image is a [maintainer task](docs/deployment.md#building-and-publishing),
not a prerequisite for every installation.

## License

Application code retains the repository's [MIT license](LICENSE). Model weights
and upstream components have their own terms; see [third-party notices](docs/notices.md).
