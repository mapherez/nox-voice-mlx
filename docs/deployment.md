# Deployment reference

Follow [Installation](getting-started.md) for the complete initial setup. All
Docker commands here run in the deployment directory containing `compose.yaml`,
`.env` and `secrets/`, unless another location is stated.

## Environment variables

Start from `deploy/.env.example`. Values specific to an installation must be
supplied by its operator; the template contains no machine addresses or accounts.

| Variable                     | Required             | Meaning                                                                  |
| ---------------------------- | -------------------- | ------------------------------------------------------------------------ |
| `NOX_VOICE_OWNER_EMAIL`      | Yes                  | Verified Google email allowed to sign in                                 |
| `NOX_VOICE_GOOGLE_CLIENT_ID` | Yes                  | Google OAuth Web application client ID                                   |
| `NOX_VOICE_CONTROLLER_URL`   | Yes                  | Gateway URL printed by Mac setup, including its port                     |
| `NOX_VOICE_WEB_PORT`         | No; default `8080`   | Host port published for local browser access                             |
| `NOX_VOICE_PUBLIC_URL`       | No                   | Additional HTTPS domain origin; localhost stays available                |
| `NOX_VOICE_ALLOWED_ORIGINS`  | No                   | Comma-separated additional trusted HTTPS origins, such as a LAN hostname |
| `NOX_VOICE_IMAGE_TAG`        | No; default `latest` | Image version selected by Compose                                        |

The controller URL always requires HTTPS and a matching server certificate.
Browser HTTP is accepted only for exact loopback names/addresses. Other browser
origins require HTTPS. Origin values contain a scheme, host and optional port,
without credentials, paths, queries or wildcards.

`NOX_VOICE_WEB_PORT` sets both the published host port and the generated local
browser origins. The container continues listening on port `8080`; changing the
host port does not change the Mac's controller or worker ports.

Local access and configured HTTPS domains work simultaneously. No environment
switch is needed between them. Each login returns to the address where it
started, and sessions are bound to that origin.

## Google OAuth

The app uses a server-side OAuth flow with a **Web application** client.
Configure its consent screen and test users as required by the Google project.
Authorized JavaScript origins are optional for this implementation.

Register an exact redirect URI for every browser address that will be used:

| Browser address                                   | Redirect URI, using the default local port       |
| ------------------------------------------------- | ------------------------------------------------ |
| `http://localhost:8080`                           | `http://localhost:8080/auth/google/callback`     |
| `http://127.0.0.1:8080`                           | `http://127.0.0.1:8080/auth/google/callback`     |
| `https://voice.example.com` (illustrative domain) | `https://voice.example.com/auth/google/callback` |

Use the configured port for local callbacks, and replace the illustrative domain
with the actual domain. The account must have a verified email equal to
`NOX_VOICE_OWNER_EMAIL`. The client secret is stored in its secret file, not in
the client ID field.

See [Google's web-server OAuth documentation](https://developers.google.com/identity/protocols/oauth2/web-server#creatingcred).

## Secret files

Compose mounts each file under `/run/secrets/` and tells the gateway where to
read it. These are ordinary host files mounted read-only for the service; they
are not baked into the image.

| Host file                         | Required                               | Purpose                                                               |
| --------------------------------- | -------------------------------------- | --------------------------------------------------------------------- |
| `secrets/ca.crt`                  | Yes                                    | Trust the Mac's private CA                                            |
| `secrets/client.crt`              | Yes                                    | Gateway client identity                                               |
| `secrets/client.key`              | Yes                                    | Private key for that client identity                                  |
| `secrets/google-client-secret`    | Yes                                    | Google OAuth client credential, plain value only                      |
| `secrets/session-secret`          | Yes                                    | Random signing key, at least 32 characters, for temporary OAuth state |
| `secrets/cloudflare-tunnel-token` | Only with the bundled `tunnel` profile | Authorize a Cloudflare connector                                      |

Generate the signing key locally during first installation and retain it across
updates. OAuth and session secrets can also be supplied through
`NOX_VOICE_GOOGLE_CLIENT_SECRET` and `NOX_VOICE_SESSION_SECRET` for secret-manager
integrations; the corresponding `_FILE` variables take precedence. The supplied
Compose uses files.

### Secret file permissions

Keep the five webapp secret files owned by the deployment user and private
(mode `600` is supported). No host `chown`, group mapping or manual permission
repair is required for the supplied webapp service.

The image starts with a small bootstrap that reads the mounted files and copies
them into its private `/tmp` tmpfs, with mode `600` inside a mode `700` directory.
It updates the internal file paths, removes supplementary groups and switches to
UID/GID `10001` before validating TLS and executing the HTTP server. These copies
are held in memory and disappear when the container stops. The original host
files are mounted read-only and are never modified.

Compose drops all capabilities, granting only `CHOWN`, `DAC_OVERRIDE`, `SETGID`
and `SETUID` for this bootstrap. The server runs without effective capabilities;
`no-new-privileges` prevents regaining privileges. Keep the supplied entrypoint,
capabilities and tmpfs settings when adapting the Compose file.

Docker must still be able to mount the source files. Missing files, invalid PEM,
encrypted keys and mismatched client keys stop startup with a specific error.
The bootstrap does not replace certificates or disable certificate validation.
The optional standalone `cloudflared` service has its own token access policy,
described below.

## Local access

The supplied Compose publishes `127.0.0.1:8080` by default. Start it with:

```sh
docker compose up -d
```

Open `http://localhost:8080` on the Docker host, using a different configured port
if applicable. Google login remains required. No Cloudflare token or reverse
proxy is needed for localhost.

### Access from another device

`localhost` always refers to the browser's own device. To use a desktop browser
against a remote Docker host, create an SSH forward **on the browser's computer**.

This is a template: replace `SSH_DESTINATION` with an SSH alias or `user@host`.
If the local webapp port is not `8080`, change both port numbers to that value.

```sh
ssh -N -L 8080:127.0.0.1:8080 SSH_DESTINATION
```

Keep SSH running and open the matching localhost URL. Forwarding itself does not
make the service public.

For direct desktop or mobile LAN access, use a browser-trusted HTTPS hostname
on a LAN reverse proxy and add that origin to `NOX_VOICE_ALLOWED_ORIGINS`.
Connect the proxy to the Compose network and forward to `http://webapp:8080`,
preserving the browser-facing Host header and supporting WebSockets. Register
the hostname's Google callback. A proxy on another machine needs a verified
HTTPS hop or another protected transport to the gateway; the default HTTP host
port is loopback-only.

Plain HTTP at a LAN IP does not provide browser microphone access; see
[browser secure-context requirements](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia#privacy_and_security).
HTTPS on the LAN does not require a public tunnel. Google authentication still contacts Google.

## Add an HTTPS domain

Set `NOX_VOICE_PUBLIC_URL` to the HTTPS origin of a domain controlled by the
deployment operator. `https://voice.example.com` is an illustrative value, not
a required domain. Add its `/auth/google/callback` URI to Google while retaining
the local callback. Localhost remains available with its existing port.

Route the domain to the gateway through a reverse proxy or optional tunnel. The
gateway selects only explicitly configured origins and does not trust forwarded
headers to add hosts. Preserve the original Host header; do not replace it with
a container name or IP.

### Optional Cloudflare Tunnel

The bundled connector runs only when the `tunnel` profile is enabled. Configure
a [remotely managed tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)
and a public hostname route to **HTTP `webapp:8080`**. The hostname must match
`NOX_VOICE_PUBLIC_URL`. Do not start another generated connector command when
using this bundled service.

Save just the tunnel token in `secrets/cloudflare-tunnel-token`. As with the other
secrets, give container group `10001` read access while retaining operator
ownership. Then start:

```sh
docker compose --profile tunnel up -d
```

An existing connector can instead join the Compose network and use the same
service address; leave the bundled profile off in that case. Audio routed
through Cloudflare passes through its network, where browser-facing TLS
terminates. The gateway-to-Mac connection keeps its separate mutual TLS.

Stop the bundled connector while leaving local access running:

```sh
docker compose --profile tunnel stop cloudflared
```

## Updates and stopping

Choose the desired tag in `NOX_VOICE_IMAGE_TAG`, then update:

```sh
docker compose up -d
```

The webapp's `pull_policy: always` checks the registry for the selected image on
each `up`; a separate pull command is unnecessary.
Add `--profile tunnel` if the bundled connector should also be running. When an
update changes `compose.yaml`, copy that version too. Compose recreates affected
containers when their image or configuration changes.

The named `webapp-data` volume retains sessions. Preserve it and the session
signing key across updates. Origin-binding schema updates may require a fresh
login at each address; they do not require reconfiguring the Mac or downloading
the model again.

Stop the webapp without deleting its data:

```sh
docker compose stop webapp
```

The Docker services use `unless-stopped` restart behavior. This is separate from
the native Mac controller, which remains manual unless its optional autostart is
explicitly enabled.

## Building and publishing

This section is for maintainers and forks. Users of the supplied published image
do not need to run a GitHub build for every installation.

The workflow in `.github/workflows/webapp.yaml` builds amd64/arm64 images. Pushes
to `main` publish `latest` and a commit tag; `vX.Y.Z` tags publish a version tag.
Pull requests check and build without publishing. Publication does not deploy
anything automatically.

The image path has this form:

```text
ghcr.io/OWNER/nox-voice-mlx-webapp:TAG
```

`OWNER` and `TAG` are placeholders. The workflow uses the repository owner's
namespace; the supplied Compose file contains the project's default image path.
A fork must align the Compose `image` with the package it publishes.

New GHCR packages can be private even when their repository is public. The
package owner must set the package visibility to Public for anonymous pulls.
Build success alone does not prove anonymous download access. See
[GitHub package visibility](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility).

To build an image locally, run **from the repository root**:

```sh
docker build -f deploy/webapp.Dockerfile -t nox-voice-webapp:local .
```

To use it, select `nox-voice-webapp:local` in the deployment's Compose `image`
field. This is an alternative to GHCR, not part of the normal installation.

## Troubleshooting

### Image pull is unauthorized

This is registry access, not Google login or Mac certificate validation.

1. Confirm the image path and tag in Compose match a successfully published
   package/version.
2. For the project's public-image deployment, the package owner opens the package
   on GitHub: **Package settings → Change visibility → Public**. Repository
   visibility and package visibility are separate.
3. Retry `docker compose up -d`. A deliberately private package instead requires
   GHCR authentication with read permission; do not use the Google client secret.

### Controller certificates cannot be loaded

`Controller certificates are missing or invalid.` in older images means the
gateway failed to load its local TLS files **before connecting to the Mac**.
Starting or restarting the Mac controller does not fix this failure. The current
image prepares private secret files automatically and validates local TLS inputs
before starting the server. Use the current Compose file and published image;
normal startup and updates both use `docker compose up -d`.

If the files themselves are missing, invalid or mismatched, the container startup
log identifies that problem without exposing their contents. Copy all three
files from the same Mac setup's `gateway/` bundle; do not regenerate the setup
just to repair host file permissions.

Local validation does not prove network reachability, server identity,
certificate expiry or that the Mac trusts this client. Those checks happen
during the subsequent TLS connection.

### Other errors

| Symptom                                     | Check                                                                                                                                                         |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `can't assign requested address` on the Mac | The setup IP must be assigned to the intended active Mac network interface                                                                                    |
| `address already in use`                    | Another process is using the configured controller or worker port                                                                                             |
| `Invalid host header`                       | Set the domain once in `NOX_VOICE_PUBLIC_URL` or `NOX_VOICE_ALLOWED_ORIGINS`; check the proxy preserves Host and the container uses the updated configuration |
| Google `redirect_uri_mismatch`              | Register the exact starting origin plus `/auth/google/callback`, including the local port                                                                     |
| Login rejected                              | Use the configured verified owner email and the intended Google client                                                                                        |
| Secret file cannot be read                  | Check filename, file content and the container's UID/GID mapping                                                                                              |
| Controller unavailable                      | Keep the Mac awake, start its controller and allow the Docker host to reach its private port                                                                  |
| Certificate validation error                | Check IP coverage, expiry and that all three client bundle files came from the same setup                                                                     |
| Model missing or tampered                   | Run `prepare-model --check` from the Mac installation directory                                                                                               |
| Microphone unavailable                      | Use localhost or trusted HTTPS, allow browser permission and start from a direct click                                                                        |

The webapp's Docker healthcheck proves only that the webapp is running. Use
**Settings → Connection → Test connection** to check the Mac hop. If image,
environment or Compose changes have not reached the running container, updating
files in the repository alone will not affect the deployment.
