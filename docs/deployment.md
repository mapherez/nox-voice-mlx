# Homelab deployment

For your first installation, follow the complete, ordered [Start here guide](getting-started.md).
This document is the deployment reference for updates and alternative arrangements.

The published image contains the compiled frontend and its Python gateway. It
does not contain the Mac engine, model weights or credentials.

## Google login

Create a Google OAuth client of type **Web application**. Its authorized redirect
URI for the default localhost deployment must exactly match:

```text
http://localhost:8080/auth/google/callback
```

For optional LAN HTTPS or external access, also register
`https://voice.example.com/auth/google/callback` with your actual hostname.
Configure the OAuth consent screen/test users as
required by your Google project. The app itself accepts only the verified email
configured in `NOX_VOICE_OWNER_EMAIL`. It uses its own login and does not require
NoX Backend or its API key.

## Files on the homelab host

Copy `deploy/compose.yaml` and `deploy/.env.example` to a deployment directory,
rename `.env.example` to `.env`, and fill in:

```env
NOX_VOICE_IMAGE_TAG=latest
NOX_VOICE_WEB_PORT=8080
# Optional domain, usable alongside localhost; leave empty for local-only use.
NOX_VOICE_PUBLIC_URL=
NOX_VOICE_OWNER_EMAIL=you@example.com
NOX_VOICE_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
NOX_VOICE_CONTROLLER_URL=https://192.168.1.50:8443
```

The webapp URL must be an origin with no path or trailing slash. HTTP is accepted
only for exact `localhost`, `127.0.0.1` or `::1` hosts; all other webapp hostnames
require HTTPS. The controller URL always requires HTTPS and must use the IP
covered by the server certificate. A plain HTTP LAN IP does not provide browser
microphone access.

Localhost access is always enabled at `NOX_VOICE_WEB_PORT`. An optional
`NOX_VOICE_PUBLIC_URL=https://voice.example.com` adds that domain alongside it.
For another trusted HTTPS LAN hostname, add its origin to
`NOX_VOICE_ALLOWED_ORIGINS` (comma-separated). Register a Google redirect URI for
every address used, including the local port. Do not switch configuration when
moving between addresses: each login returns to its starting origin, and cookies,
CSRF checks and streaming sessions are bound to that origin.

Create `secrets/` alongside Compose and populate:

```text
secrets/
├── ca.crt                   # From the Mac's gateway bundle
├── client.crt               # From the same bundle
├── client.key               # From the same bundle; keep private
├── google-client-secret     # Google OAuth client secret, plain text
├── session-secret           # Random secret, at least 32 characters
└── cloudflare-tunnel-token  # Required when using the bundled tunnel profile
```

Generate the session secret locally, for example:

```sh
umask 077
mkdir -p secrets
openssl rand -hex 32 > secrets/session-secret
```

Do not paste credentials into the repository, Dockerfile, browser storage or
controller URL. Google/session credentials are read from Docker secrets. Direct
`NOX_VOICE_GOOGLE_CLIENT_SECRET` and `NOX_VOICE_SESSION_SECRET` environment
variables are supported for integrations with an existing secret manager; `_FILE`
variables take precedence.

The webapp runs as UID/GID **10001**. On Linux, ensure the secret files are
readable by that UID and otherwise private. For a dedicated secrets directory:

```sh
chmod 700 secrets
chmod 600 secrets/*
sudo chown 10001:10001 secrets/*
chmod 600 .env
```

These ownership commands apply only to the deployment's dedicated directory.
Keep the directory itself owned by your deployment user; only the individually
mounted secret files need UID 10001 ownership.
Local Compose secret mounts can preserve host ownership, so declarations of
`mode` in Compose alone are not a substitute for correct file permissions.

## Localhost: no reverse proxy or tunnel

After preparing `.env` and the five required secret files:

```sh
docker compose pull
docker compose up -d
```

Open **http://localhost:8080** on the Docker host and sign in with Google. The
published port binds only to `127.0.0.1`. No Cloudflare token is needed and its
profile is not started. For a different local port, change only
`NOX_VOICE_WEB_PORT`, then register the matching local Google redirect URI.
The public domain, if configured, stays unchanged.

If your browser runs on a different computer, `localhost` refers to that computer,
not the homelab. For a local test through SSH, run on the browser's computer:

```sh
ssh -N -L 8080:127.0.0.1:8080 YOUR_USER@YOUR_HOMELAB_HOST
```

Then open the same localhost URL. For direct LAN access on a MacBook/iPhone,
configure a browser-trusted HTTPS hostname on a LAN reverse proxy and add
its origin to `NOX_VOICE_ALLOWED_ORIGINS`. A public tunnel is not required for LAN
HTTPS. Google login still requires an Internet connection to Google.

## Optional external access: Cloudflare Tunnel

When enabling external access, use the supplied connector in the same Docker network, so the
connector-to-webapp hop does not travel unencrypted across the LAN.

1. Set `NOX_VOICE_PUBLIC_URL=https://voice.example.com` in `.env` and register
   that hostname's `/auth/google/callback` URI with Google. Localhost remains
   available, so keep the local callback registered as well.
2. Create a named tunnel in Cloudflare and put its token in the secret file.
3. Configure its public hostname `voice.example.com` to service
   **`http://webapp:8080`**.
4. Start:

```sh
docker compose --profile tunnel pull
docker compose --profile tunnel up -d
```

The host port remains loopback-only. Your existing `cloudflared` connector can
instead join this Docker network and route to the same service name; omit the
`tunnel` profile in that case. If you keep a connector on another machine, use a
properly verified HTTPS reverse proxy for that LAN hop rather than disabling TLS
verification or exposing the webapp with an unprotected HTTP port.

The hostname remains reachable from outside your home, but every protected API
and WebSocket requires the Google-authenticated application session. Cloudflare
Access is optional; the app does not rely on it. Audio passes through Cloudflare,
where the browser-facing TLS connection terminates.

## Initial use

Start the controller on the Mac, open the configured webapp URL and sign in. In Settings:

1. Check the Mac address and click **Test connection**.
2. Click **Start engine** and wait for **Ready**.
3. Close Settings and click **Start conversation**. Allow microphone access.

The sphere responds only to generated audio being played. Mute disables the
microphone track and sends silence; it does not mute the assistant's output.
Leaving/backgrounding the page ends capture. Start a new conversation explicitly
after an interruption. Settings remains available while the engine is stopped.

## Images and updates

The workflow publishes to `ghcr.io/mapherez/nox-voice-mlx-webapp`. Pushes to `main`
publish `latest` and a commit tag; a `vX.Y.Z` Git tag also publishes `X.Y.Z`.
Pull requests validate and build without publication.

The first successful publication creates a package. Its owner must set the GHCR
package visibility to **Public** once if it starts private. Until the workflow
has run, there is no published image to pull. To build locally instead:

```sh
docker build -f deploy/webapp.Dockerfile -t nox-voice-webapp:local .
```

Choose a version/commit tag in `.env` for controlled updates, then run:

```sh
docker compose pull
docker compose up -d
```

Publishing an image never deploys it automatically. The named `webapp-data`
volume retains browser sessions. Do not remove it when updating; deleting it
signs out all devices. Session expiry is twelve hours and logout revokes the
current device's session. Keep the session secret stable across updates.
If using the optional tunnel, add `--profile tunnel` to the update commands.

## Troubleshooting

- **Mac unavailable:** check that it is awake, its controller is running, the LAN
  IP is unchanged, VLAN/firewall routing allows the controller port and all three
  client bundle files are mounted/readable.
- **Certificate error:** check expiry and the IP in the certificate. Replace the
  bundle through the documented rotation process; do not disable verification.
- **Model missing:** run preparation on the Mac. Mounting model files in Docker
  does not make them available to the native engine.
- **Login rejected:** check the exact owner email, Google client settings and
  configured browser origin (including the localhost port). Other Google
  accounts are deliberately refused.
- **Microphone unavailable:** use localhost HTTP or a trusted HTTPS hostname, check browser permission,
  start from a direct user click and ensure another tab is not using the engine.
- **Controller request fails while the panel loads:** the panel and controller
  are separate. The local `8080` webapp healthcheck does not prove Mac readiness.
- **Client disconnects during a long call:** Cloudflare/network interruptions can
  close WebSockets. Capture stops and a new conversation must be started.
