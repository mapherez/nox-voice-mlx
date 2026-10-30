# Installation

Follow this guide in order. Complete each checkpoint before continuing. The
reference documents are for later configuration and maintenance.

## Before starting

You need:

- An **Apple Silicon Mac**, with native ARM64 Python support and enough memory
  and disk for your selected model. PersonaPlex downloads about 17.1 GB; LFM3
  downloads about 2.43 GB including its runtime. Allow additional space for
  dependencies and preparation. Memory use varies with model and precision.
- **uv** on the Mac. Use the [official installation instructions](https://docs.astral.sh/uv/getting-started/installation/)
  if it is not installed.
- A **Docker host** with Docker Engine or Docker Desktop, Docker Compose and the
  OpenSSL command-line tool. The webapp image supports amd64 and arm64.
- A Google account and OAuth client for browser sign-in. PersonaPlex also needs a
  Hugging Face account, accepted model access terms and a read token. LFM3
  downloads directly without a token.
- A private network connection from the Docker host to the Mac. They may be the
  same physical computer, but Docker must be able to reach the Mac's LAN IP.

This guide uses the default ports: **8443** for the Mac controller, **8000** for
its private worker and **8080** for the browser. The controller and worker ports
must differ. Node/npm is only needed for frontend development, not installation.

## 1. Install the native Mac components

**Where:** Terminal on the Mac that will run the model.

Clone this repository or download and extract it using the project's **Code**
button. Keep it in any directory you choose. From its root
(the folder containing `README.md`, `mac/` and `deploy/`), run:

```sh
cd mac
```

Alternatively, copy the contents of `mac/` to a directory of your choice before
installing, and open Terminal inside that copy. Exclude any existing `.venv/`.
The installation directory must contain `pyproject.toml`, `uv.lock`, `controller/`,
`engine/` and `weights/`.

**Run all remaining Mac commands from this installation directory.**

```sh
uv --version
uv python install 3.12
uv sync --locked --extra engine --python 3.12
```

**Checkpoint:** installation finishes successfully. A hidden `.venv/` folder is
created in the installation directory.

## 2. Configure and start the Mac controller

**Where:** the same Mac terminal and installation directory.

Find the Mac's actual LAN IPv4 address in **System Settings → Network → the
active connection → Details → TCP/IP**. Choose an address assigned to this Mac
that the Docker host can reach. A DHCP reservation helps keep it stable.

In **zsh**, enter the address at this prompt:

```zsh
read -r "NOX_MAC_IP?Mac LAN IPv4 address: "
```

Create the configuration and certificates:

```sh
uv run --locked --extra engine nox-voice setup --host "$NOX_MAC_IP" --port 8443
```

Run setup once. If it reports an existing configuration, do not delete it or
repeat setup to fix unrelated Docker or login errors; consult the
[Mac reference](mac-setup.md).

Save these two outputs for the Docker deployment:

- **Gateway URL:** the exact HTTPS controller URL printed by setup.
- **Client directory:** the directory containing `ca.crt`, `client.crt` and
  `client.key`. Only these three files are transferred to the Docker host.

The default client directory is:

```text
~/Library/Application Support/NoX Voice/tls/gateway/
```

`~` means the current user's home directory. In Finder, use **Go → Go to Folder**
and paste that path to open the normally hidden Library directory.

Start the controller manually:

```sh
uv run --locked --extra engine nox-voice controller
```

**Checkpoint:** it shows `Application startup complete` and remains running
without a bind error. `Engine is stopped` is expected at this stage.

Leave this terminal open and the Mac awake. **Ctrl+C stops the controller and
its worker.** This command does not enable startup at login. Do not open the
controller URL in a browser; it is a private API requiring a client certificate.

## 3. Prepare the Docker deployment directory

**Where:** the Docker host. Use a new terminal in a dedicated directory of your
choice; this is the **deployment directory** for all Docker commands below.

Copy these files from the repository:

| Repository file       | Destination in the deployment directory |
| --------------------- | --------------------------------------- |
| `deploy/compose.yaml` | `compose.yaml`                          |
| `deploy/.env.example` | `.env`                                  |

Create the secrets directory:

```sh
umask 077
mkdir -p -m 700 secrets
```

Securely transfer the three files from the Mac's client directory into it:

```text
secrets/ca.crt
secrets/client.crt
secrets/client.key
```

The `gateway/` directory at the repository root is application source code; it
is not the generated certificate directory. The model and engine remain on the Mac.

**Checkpoint:** `compose.yaml`, `.env` and those three files are in the deployment
directory. Do not copy the Mac's CA private key or server private key.

## 4. Configure Google login and the environment

**Where:** Google Cloud in a browser, then `.env` on the Docker host.

Create an OAuth client with application type **Web application** in a Google
Cloud project. Complete the consent configuration and add the allowed account
as a test user if the project requires it. Follow the
[official Google instructions](https://developers.google.com/identity/protocols/oauth2/web-server#creatingcred)
if needed.

Register this **Authorized redirect URI** for the default local port:

```text
http://localhost:8080/auth/google/callback
```

If you choose a different `NOX_VOICE_WEB_PORT`, use that same port in the local
callback. Authorized JavaScript origins are not required by this server-side
login flow.

Keep the Google **client ID** and **client secret**. They are different values.
Edit the copied `.env` template:

| Variable                     | What to enter                                           |
| ---------------------------- | ------------------------------------------------------- |
| `NOX_VOICE_WEB_PORT`         | The local browser port; keep `8080` initially           |
| `NOX_VOICE_OWNER_EMAIL`      | The verified Google email allowed to sign in            |
| `NOX_VOICE_GOOGLE_CLIENT_ID` | The client ID from Google                               |
| `NOX_VOICE_CONTROLLER_URL`   | The exact Gateway URL printed by Mac setup              |
| `NOX_VOICE_PUBLIC_URL`       | Leave empty for now; an HTTPS domain can be added later |
| `NOX_VOICE_ALLOWED_ORIGINS`  | Leave empty unless adding another trusted HTTPS address |

The controller URL identifies the **Mac**, not the Docker host. The webapp port
does not change the Mac controller's port.

**Checkpoint:** the three required values (owner email, client ID and controller
URL) have been filled in. The Google client secret is saved in the next step,
not in `.env`.

## 5. Create the two remaining secret files

**Where:** the deployment directory on the Docker host.

Use **Bash** for the hidden-input command below. If using another shell, run
`bash` first and stay in it for this step. Run the prompt line on its own, paste only
the Google client secret at the prompt, then press Enter:

```bash
read -r -s -p 'Google OAuth client secret: ' nox_google_secret
```

Save its value and generate a separate random signing key:

```sh
printf '\n'
umask 077
printf '%s' "$nox_google_secret" > secrets/google-client-secret
unset nox_google_secret
openssl rand -hex 32 > secrets/session-secret
```

The Google secret file contains only the secret value, without `NAME=`, quotation
marks or JSON. Generate `session-secret` once and keep it during updates.

| File                          | Purpose                                       |
| ----------------------------- | --------------------------------------------- |
| `ca.crt`                      | Verify the Mac controller's certificate       |
| `client.crt` and `client.key` | Authenticate the gateway to the Mac           |
| `google-client-secret`        | Authenticate the app's OAuth client to Google |
| `session-secret`              | Sign temporary login-state cookies            |

Keep these files private and owned by your deployment user. There is no need to
change them to UID/GID `10001`, run `chown`, or grant the container group access.
At startup the image copies the five webapp secrets into private temporary memory
and switches to UID/GID `10001` before starting the server. The source files are
mounted read-only and retain their ownership and permissions.

**Checkpoint:** the deployment directory contains exactly the required files:

```text
deployment-directory/
├── compose.yaml
├── .env
└── secrets/
    ├── ca.crt
    ├── client.crt
    ├── client.key
    ├── google-client-secret
    └── session-secret
```

Cloudflare is not needed for this local installation.

## 6. Start the webapp

**Where:** the deployment directory on the Docker host.

```sh
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose ps
```

**Checkpoint:** `webapp` reports **healthy**. Only the webapp service starts;
the optional tunnel profile remains off. If image pulling reports `unauthorized`,
use [GHCR troubleshooting](deployment.md#image-pull-is-unauthorized) before
changing anything on the Mac.

## 7. Open the app and start a conversation

**Where:** a browser on the Docker host.

1. Open **http://localhost:8080**, using the configured port if you changed it.
2. Sign in with the Google account in `NOX_VOICE_OWNER_EMAIL`.
3. Open **Settings → General → Test connection**.
4. In **Settings → Models**, select PersonaPlex or LFM3-Audio.
5. For PersonaPlex, open **Credentials**, follow **Model access terms** and
   **Create a token**, save a read token and click **Test access**. LFM3 does not
   require a token.
6. In **Models**, click **Download**. The Download Manager shows file/total
   progress, speed, preparation and verification. You can close the window;
   downloads continue on the Mac. Use **Cancel download** or **Retry / resume**
   there when needed. No manual file downloads are required.
7. Click **Load** once the files are available. Wait for **Model loaded**.
8. Close Settings and click **Start conversation** for PersonaPlex, or **Connect
   LFM3** for LFM3. Allow microphone access when prompted.

**Download** saves files; **Load** loads the model into memory; **Eject** releases
that memory while keeping the downloaded files. Selecting a model never starts a
download or inference. **Open folder** opens Finder on the controller Mac, even
when the browser is on another device; a graphical login on that Mac is required.

PersonaPlex supports continuous voice conversation. LFM3 provides push-to-talk
transcription and reading supplied text aloud. For LLM answers and MCP actions,
configure and enable [Executor & MCP](executor.md). The sphere reacts only to
assistant playback.
**End conversation** releases the microphone. **Ctrl+C in the Mac terminal**
stops the controller, its model worker and active downloads. Interrupted downloads
can be resumed explicitly after restarting; models never load automatically.

### If the browser is on another device

`localhost` means the device running the browser, not a remote Docker host.
For a desktop browser, open an SSH forward from that computer to the Docker host
(keep its local port equal to `NOX_VOICE_WEB_PORT`), then use the localhost URL.
The [deployment reference](deployment.md#access-from-another-device) has the steps.

For direct access from another LAN device, including mobile browsers, configure
a trusted HTTPS address. Plain HTTP at a LAN IP does not provide microphone
access. This does not require public exposure.

## Optional: add an HTTPS domain

After local use works, follow [domain access](deployment.md#add-an-https-domain).
Configure the domain once and register its Google callback. Localhost remains
available alongside it; there is no need to switch `.env` values between local
and domain access. Cloudflare Tunnel is optional.

For any failed checkpoint, use [troubleshooting](deployment.md#troubleshooting)
and report the command and error with secrets removed. Model performance and
browser compatibility should be checked on the target hardware; see
[validation](validation.md).
