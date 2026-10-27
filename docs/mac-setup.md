# Native Mac setup

The controller and engine run natively on Apple Silicon, outside Docker. The
controller stays lightweight; only its child worker loads model libraries and
weights. Use native ARM64 Python 3.12, not Rosetta Python.

## 1. Install

Clone this repository, including the pinned NVIDIA reference submodule if you
want to inspect it:

```sh
git clone --recursive https://github.com/mapherez/nox-voice-mlx.git
cd nox-voice-mlx/mac
uv sync --locked --extra engine
```

The production worker does not install or execute the NVIDIA server's old pinned
requirements. `npm install` is not needed on the Mac engine host.

For a small native compatibility/security smoke test without downloading model
weights, run from the repository root after installing the engine extra:

```sh
mac/.venv/bin/python tests/mac/native_smoke.py
```

It validates a small MLX model at all five precisions, cache isolation and
restricted voice loading. It does not replace testing the actual 7B checkpoint.

## 2. Prepare the model

Log in to Hugging Face and accept the access terms at:

https://huggingface.co/nvidia/personaplex-7b-v1

Create a fine-grained read token authorized for that model. Set `HF_TOKEN` in your
local shell without committing it or putting its value in a command argument.
For example, use your shell's hidden-input mechanism or a local secret manager.

From `mac/`, run:

```sh
uv run --locked --extra engine nox-voice prepare-model
uv run --locked --extra engine nox-voice prepare-model --check
```

This downloads only the official weights, codec, text tokenizer and voice archive
to `mac/weights/`. The checked-in text tokenizer will be verified against the
official version. Voice archive extraction accepts only the 18 named `.pt` files
and rejects traversal, links, duplicates and oversized entries.

The first preparation resolves the official repository's current commit and
records it in `manifest.json`; runtime verifies that exact manifest offline.
For a controlled re-install, supply `--revision FULL_40_CHARACTER_COMMIT_HASH`.
Stop the engine before replacing prepared files. A custom location is supported
with `prepare-model --weights-dir /absolute/path` and the matching setup option.

You can unset `HF_TOKEN` after preparation. The worker strips inherited token and
secret variables and sets `HF_HUB_OFFLINE=1` and `HF_HUB_DISABLE_TELEMETRY=1`.

## 3. Create the controller configuration and certificates

Reserve a private IP for this Mac in your router/DHCP server. Replace this example
with that IP:

```sh
uv run --locked --extra engine nox-voice setup --host 192.168.1.50
```

Defaults:

| Setting | Default |
| --- | --- |
| Private HTTPS controller | `https://192.168.1.50:8443` |
| Private worker | `http://127.0.0.1:8000` |
| Controller configuration | `~/Library/Application Support/NoX Voice/controller.json` |
| Certificate directory | `~/Library/Application Support/NoX Voice/tls/` |
| Private voice/persona settings | Alongside the controller configuration |

Use `--port`, `--worker-port` and `--weights-dir` during setup if needed. Ports
must be different. The bind address must be a specific private IP, not
`0.0.0.0`. If your network has VLANs, permit the homelab gateway to reach the Mac's
controller port. The worker port must remain loopback-only.

The setup command prints the gateway bundle directory containing exactly:

```text
gateway/ca.crt
gateway/client.crt
gateway/client.key
```

Copy **only these three files** securely to the homelab. Keep `ca.key`,
`server.key` and the server certificate on the Mac. Never install this CA into a
browser or disable certificate verification: the gateway trusts it explicitly,
and client certificates authorize gateway access to the controller.

## 4. Run manually

```sh
uv run --locked --extra engine nox-voice controller
```

The controller starts with the model stopped. Use the webapp's Settings drawer to
start it. Closing this terminal stops the manual controller and its worker.

On startup the engine verifies assets, loads the model, applies quantization,
loads the codec, warms up and conditions the selected voice. Loading can take a
while; the webapp continues showing its progress and Stop remains available.
Startup times out after five minutes by default. Inspect the native installation
and asset verification if it fails; there is no automatic crash-restart loop.

## 5. Optional startup after login

Stop the manual controller first, then run:

```sh
uv run --locked --extra engine nox-voice autostart enable
uv run --locked --extra engine nox-voice autostart status
```

This installs a per-user LaunchAgent. It does not install a root daemon and does
not load the model automatically. The app becomes reachable after you log in and
while the Mac is awake. To return to manual operation:

```sh
uv run --locked --extra engine nox-voice autostart disable
```

Keep this repository and its virtual environment at their installed paths while
autostart is enabled. Disable it before moving or replacing the installation.
For a separate configuration, place `--config /absolute/path/controller.json`
**before** the subcommand.

## Certificate renewal

Client and server certificates expire after one year; the private CA lasts ten
years. Before expiry, stop the old controller, create a setup with a **new parent
directory**, replace the gateway's client bundle, update the controller's config
path and restart both components. Never copy the CA key to the homelab. To revoke
a lost client bundle immediately, replace the CA and both certificates this way.

Existing certificate directories are never overwritten automatically.
