# Start here: Mac → image → homelab → browser

Follow this guide from top to bottom for the first installation. The other
documents are reference material; you do not need to switch between them.

There are only two installations:

| Machine | What runs there | What you install |
| --- | --- | --- |
| Mac Studio | Controller and PersonaPlex engine | Python dependencies with `uv`, model files and certificates |
| Homelab Docker host | Webapp, including the frontend and gateway | Compose file, environment file and secrets; the image comes from GHCR |
| MacBook / iPhone | Browser | Nothing from this repository |

The `frontend/` directory is for development. **You do not need Node, npm, or a
separate frontend installation to use the published image.**

Keep the default controller port **8443** for this first installation. In the
examples below, replace `192.168.1.50` with your Mac Studio's private LAN IP and
`voice.example.com` with your chosen webapp hostname.

## 1. On the Mac Studio: prepare the native service

### 1a. Install the dependencies

Open Terminal on the **Mac Studio**. Use the existing repository, with the new
application files present. If it is in the usual location:

```sh
cd ~/Documents/GitHub/nox-voice-mlx/mac
uv --version
```

If `uv` is missing and Homebrew is already installed, install it with
`brew install uv`. Otherwise use the [official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/),
then reopen Terminal and return to this directory.

Install the Mac dependencies:

```sh
uv python install 3.12
uv sync --locked --extra engine --python 3.12
```

**Checkpoint:** the command finishes successfully and `mac/.venv/` exists.
Stay in the `mac/` directory for all remaining Mac commands.

### 1b. Download the model once

In your browser, sign in to [the official PersonaPlex model page](https://huggingface.co/nvidia/personaplex-7b-v1)
and accept its access terms yourself. Create a Hugging Face read token authorized
to download that model; [token instructions](https://huggingface.co/docs/hub/security-tokens).
Allow space for approximately 17 GB of model assets, plus dependencies and the
download cache.

Back in the Mac's default **zsh** Terminal, enter the token at a hidden prompt:

```zsh
read -r -s "HF_TOKEN?Hugging Face token: "
printf '\n'
export HF_TOKEN
uv run --locked --extra engine nox-voice prepare-model
uv run --locked --extra engine nox-voice prepare-model --check
unset HF_TOKEN
```

Paste only the token at that prompt. Do not put it in `.env`, a command argument,
the repository or this chat. The download can take a while.

**Checkpoint:** verification finishes with `Model verified at official revision`.
If the download or verification fails, resolve that error before continuing.

### 1c. Create the configuration and certificates

Find the Mac Studio's LAN IP in **System Settings → Network → your active
connection → Details → TCP/IP**. Reserve that address in your router's DHCP
settings so that it stays the same. Use that IP in this command:

```sh
uv run --locked --extra engine nox-voice setup --host 192.168.1.50 --port 8443
```

Run setup **once**. If you already ran it successfully, keep the existing
configuration instead of repeating it. Its default location is:

```text
~/Library/Application Support/NoX Voice/controller.json
```

You do not edit `config.py`. The JSON file is the installed configuration.
The `port` in that file and the port in the homelab's controller URL must match.
Changing only the port requires a controller restart; changing the IP also
requires a server certificate for the new IP.

**Checkpoint:** setup prints the controller URL and a gateway bundle directory.
With the default configuration, that directory is:

```text
~/Library/Application Support/NoX Voice/tls/gateway/
```

It contains `ca.crt`, `client.crt` and `client.key`. You will copy these three
files to the homelab in step 3. Keep the rest of `tls/` on the Mac.

### 1d. Start the controller

```sh
uv run --locked --extra engine nox-voice controller
```

**Checkpoint:** Terminal shows the controller URL and says the engine is stopped.
That is expected: the model will be started from Settings later.

Leave this terminal open and the Mac awake. For now, use manual startup; configure
optional automatic startup only after the first successful conversation.
Do not test this private URL by opening it in a browser: it requires the gateway's
client certificate.

## 2. On GitHub: make the webapp image available

The homelab cannot pull an image before the first successful publication.
Creating the workflow file locally does not publish it.

1. The application changes, including `.github/workflows/webapp.yaml`, must be
   committed and pushed to `main` in your GitHub repository.
2. In the repository's **Actions** tab, wait for **Webapp checks and Docker image**
   to finish successfully. A push to `main` triggers it.
3. In your GitHub Packages settings, make `nox-voice-mlx-webapp` **Public** if it
   was initially created private.

**Checkpoint:** this image and tag are published and publicly accessible:

```text
ghcr.io/mapherez/nox-voice-mlx-webapp:latest
```

This guide does not commit, push or publish anything automatically.

## 3. On the homelab: configure and start the webapp

This first-install path uses the **Cloudflare connector included in Compose**.
It assumes a Linux host with Docker Engine and the `docker compose` plugin, plus
a domain managed by Cloudflare.

### 3a. Prepare the deployment directory

On the **homelab host**, create a dedicated directory, for example
`~/nox-voice/`. Copy these files from the updated repository into it:

| Repository file | Destination on the homelab |
| --- | --- |
| `deploy/compose.yaml` | `~/nox-voice/compose.yaml` |
| `deploy/.env.example` | `~/nox-voice/.env` |

In a terminal on the homelab:

```sh
cd ~/nox-voice
umask 077
mkdir -p secrets
```

Using a secure file transfer such as SFTP, copy **only** the three files from the
Mac's `tls/gateway/` directory into `~/nox-voice/secrets/`:

```text
secrets/ca.crt
secrets/client.crt
secrets/client.key
```

The engine and model files stay on the Mac. You do not copy `frontend/`, install
npm, or clone the whole repository on the homelab.

### 3b. Configure Google login

In your Google Cloud project, create an OAuth client with application type
**Web application**. Configure its consent screen and add your account as a test
user if the project requires it. Set this exact authorized redirect URI, using
your chosen hostname:

```text
https://voice.example.com/auth/google/callback
```

Keep its **client ID** and **client secret** for the next steps. The ID goes in
`.env`; the secret goes in a file. See the [official Google instructions](https://developers.google.com/identity/protocols/oauth2/web-server#creatingcred)
if creating a client for the first time.

Edit `~/nox-voice/.env` on the homelab:

```env
NOX_VOICE_IMAGE_TAG=latest
NOX_VOICE_PUBLIC_URL=https://voice.example.com
NOX_VOICE_OWNER_EMAIL=your-google-email@example.com
NOX_VOICE_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
NOX_VOICE_CONTROLLER_URL=https://192.168.1.50:8443
```

The last line must use the **Mac Studio's IP and controller port**, not the
homelab's IP. The owner email must be the verified Google account you will use.

### 3c. Configure the Cloudflare route

In Cloudflare, [create a remotely managed Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/).
Copy the tunnel token for the connector. The Compose service will run the
connector, so you do not need to run the generated Docker command separately.

Add a public hostname route with:

| Field | Value |
| --- | --- |
| Public hostname | `voice.example.com` — the same hostname used above |
| Service type | HTTP |
| Service URL | `webapp:8080` |

The hostname is HTTPS for the browser. The HTTP hop above stays inside the
Compose Docker network. Audio passes through Cloudflare, which terminates the
browser-facing TLS connection.

### 3d. Save the remaining secrets

In **Bash on the homelab**, from `~/nox-voice/`, enter each secret at the hidden
prompt. Paste just its value, without quotation marks or a surrounding command:

```bash
umask 077
read -r -s -p 'Google OAuth client secret: ' nox_google_secret
printf '\n'
printf '%s' "$nox_google_secret" > secrets/google-client-secret
unset nox_google_secret
read -r -s -p 'Cloudflare tunnel token: ' nox_tunnel_token
printf '\n'
printf '%s' "$nox_tunnel_token" > secrets/cloudflare-tunnel-token
unset nox_tunnel_token
openssl rand -hex 32 > secrets/session-secret
```

Run the secret generation only during initial setup; retain `session-secret`
when updating the app. Check that all six files now exist:

```text
nox-voice/
├── compose.yaml
├── .env
└── secrets/
    ├── ca.crt
    ├── client.crt
    ├── client.key
    ├── google-client-secret
    ├── session-secret
    └── cloudflare-tunnel-token
```

Give the containers' UID access to this dedicated secrets directory:

```sh
chmod 700 secrets
chmod 600 secrets/*
sudo chown 10001:10001 secrets/*
chmod 600 .env
```

Keep the directory itself owned by your deployment user. Only the files need
UID 10001 ownership, since Compose mounts each file individually.

### 3e. Pull and start

Still on the homelab, from `~/nox-voice/`:

```sh
docker compose --profile tunnel config --quiet
docker compose --profile tunnel pull
docker compose --profile tunnel up -d
docker compose --profile tunnel ps
```

**Checkpoint:** `webapp` is healthy and `cloudflared` is running. If the image
cannot be pulled, check step 2 before troubleshooting the Mac.
The homelab must be able to reach the Mac's LAN IP on TCP port 8443; permit that
connection in your LAN/VLAN/firewall rules if needed.

## 4. On the MacBook or iPhone: use the app

1. Open `https://voice.example.com` and sign in with the configured Google account.
2. Open **Settings → Connection → Test connection**. It should confirm the Mac
   controller is reachable.
3. In **Settings → Engine**, click **Start engine** and wait for **Ready**.
4. Close Settings, click **Start conversation**, and allow microphone access.

The Mac terminal stays open during this first test. The engine starts stopped
after each controller startup; that does not mean the installation failed.

If a checkpoint fails, stop at that checkpoint and report its step number and
error text, with secrets removed. Do not rerun setup or replace certificates to
fix an unrelated image, login or tunnel error.

After the first successful call, use [Mac reference](mac-setup.md) for optional
autostart and certificate renewal, or [deployment reference](deployment.md) for
updates and alternative connector arrangements.
