# Mac reference

For the first installation, follow [Installation](getting-started.md). This page
covers configuration and maintenance of the native Mac service.

## Installation directory

The `mac/` component is independent of the Docker webapp. It can run from a
repository checkout or from a standalone copy in a directory of your choice.
It contains `pyproject.toml`, `uv.lock`, `controller/`, `engine/` and `weights/`.
Commands on this page run from that installation directory.

The engine requires Apple Silicon and native ARM64 Python 3.12. Install the
`engine` extra with the supplied uv lockfile; the standalone NVIDIA reference
server and its requirements are not used by this application.

## Configuration and data locations

| Item                       | Default location                                              |
| -------------------------- | ------------------------------------------------------------- |
| Controller configuration   | `~/Library/Application Support/NoX Voice/controller.json`     |
| Voice/persona settings     | `~/Library/Application Support/NoX Voice/voice-settings.json` |
| TLS files                  | `~/Library/Application Support/NoX Voice/tls/`                |
| Gateway certificate bundle | `~/Library/Application Support/NoX Voice/tls/gateway/`        |
| Model assets               | `weights/` in the installation directory                      |
| Python environment         | `.venv/` in the installation directory                        |

`~` means the current user's home directory. These are application defaults, not
a required location for the repository. Finder's **Go → Go to Folder** opens the
hidden Library path. Installed configuration belongs in `controller.json`;
`controller/config.py` defines defaults and validation, not per-install settings.

To use another configuration file, pass `--config` **before** the subcommand:

```text
nox-voice --config CONFIG_FILE controller
```

This is syntax notation: replace `CONFIG_FILE` with the chosen path. Setup uses
that file's parent directory for the new installation's data and certificates.

## Ports and network address

| Setting           | Default       | Purpose                                  |
| ----------------- | ------------- | ---------------------------------------- |
| `port`            | `8443`        | Private HTTPS/mTLS controller API        |
| `worker_port`     | `8000`        | Authenticated worker on `127.0.0.1` only |
| `startup_timeout` | `300` seconds | Maximum engine startup wait              |

Use a private IP actually assigned to the Mac and reachable from the Docker host.
If more than one interface is active, check the address of the intended
connection. Setup does not assign an IP to an interface. Reserve the address in
DHCP or otherwise keep it stable.

Both ports must be unused, between 1024 and 65535, and different from each other.
Setup accepts `--port` and `--worker-port`. After installation, stop the
controller before editing its JSON configuration and restart afterward.

Changing the controller port also requires updating `NOX_VOICE_CONTROLLER_URL`
on the Docker host. Changing the IP additionally requires a server certificate
covering the new address. The worker remains loopback-only in either case.

## Manual operation

Start the controller:

```sh
uv run --locked --extra engine nox-voice controller
```

Stop it with **Ctrl+C in that terminal**. The controller terminates its model
worker on shutdown. The model starts stopped each time; **Settings → Engine**
controls Start/Stop/Restart while the controller is running.

Starting the controller does not enable login autostart. Keep the Mac awake for
remote use. The webapp does not wake the Mac or start a stopped controller.

## Model assets and precision

`prepare-model` downloads only the official model, codec, tokenizer and voice
archive. It pins an immutable revision, validates hashes, extracts the 18 known
voices and writes `manifest.json`. `prepare-model --check` verifies local assets
offline. The engine verifies the manifest again before loading.

```text
weights/
├── model.safetensors
├── tokenizer-e351c8d8-checkpoint125.safetensors
├── tokenizer_spm_32k_3.model
├── voices.tgz
├── manifest.json
└── voices_cpu/
    └── named voice prompt files
```

Use `--revision` with a full official commit hash for reproducible preparation.
Use `--weights-dir` on both preparation and setup to select a custom model
directory. Existing installations store its absolute path as `weights_dir`.
Stop the engine before replacing assets or changing that path.

PersonaPlex **7B** means approximately seven billion parameters. The selectable
4/5/6/8-bit settings quantize their weights; they do not change the parameter
count. Full precision retains the original weight precision. Memory, speed and
quality should be measured on the target Mac; there is no universal latency
guarantee. A precision change reloads the model and requires ending the current
conversation.

## Moving an installation

1. Stop the controller. If autostart was explicitly enabled, disable it first.
2. Copy or move the installation directory, excluding `.venv/`. Prepared weights
   can move with it; no repeat download is needed if their manifest verifies.
3. In the destination, recreate the environment:

   ```sh
   uv sync --locked --extra engine --python 3.12
   ```

4. If setup was already completed, update `weights_dir` in `controller.json` to
   the absolute model directory at its new location. Keep the existing
   configuration and certificates if the controller IP is unchanged.
5. Start the controller from the new installation. Re-enable autostart only if
   that behavior is wanted.

Virtual environments contain installation paths and should be recreated rather
than copied. A LaunchAgent also records the interpreter and working directory.

## Optional automatic startup

This section is optional. Skip it to keep the Mac controller fully manual.

After stopping any manual controller, explicitly enable startup after login:

```sh
uv run --locked --extra engine nox-voice autostart enable
```

Check or disable that setting:

```sh
uv run --locked --extra engine nox-voice autostart status
uv run --locked --extra engine nox-voice autostart disable
```

Enabling installs a per-user LaunchAgent, not a root daemon. It starts the
controller after login while the Mac is awake; it does not start the model.
Keep the installation at its recorded path until autostart has been disabled.

## Certificates and renewal

The gateway bundle contains only `ca.crt`, `client.crt` and `client.key`. Keep
`ca.key` and `server.key` on the Mac. The browser does not use this private CA;
the gateway trusts it directly and presents its client certificate.

Client and server certificates expire after one year; the CA lasts ten years.
Before expiry, stop the controller and prepare a replacement installation with
a new configuration parent directory. Transfer its new gateway bundle to the
Docker host, select the replacement configuration, then restart both services.
Retain the old files privately until the replacement is verified. A lost client
bundle can be revoked by replacing the CA and certificates through this process.

Setup refuses existing configuration files and nonempty certificate directories.
Use `setup --help` to inspect its options; do not delete certificates as a remedy
for image-pull, Google login or webapp Host errors.
