# Install CatProof Community Edition

## Requirements

- Docker Engine with Compose v2, or Docker Desktop running Linux containers.
- A free local port 8443 and space for the application, PostgreSQL and Docker volumes.
- Access to download the application and upstream images. Setup does not need a CatProof Cloud account.

Published images target Linux amd64 and arm64. CI records the architectures actually built and tested. Docker Desktop on macOS and Windows requires separate host validation; do not infer host support from a successful Linux build.

## Download

Download the source archive or installation bundle from [GitHub Releases](https://github.com/cat-proof/catproof-community/releases). Extract it into a directory you will keep. Moving to a differently named Compose project or deleting the volumes can make the existing data unavailable. Keep the project name `catproof-community` for this installation.

Alternatively:

```sh
git clone https://github.com/cat-proof/catproof-community.git
cd catproof-community
./community.sh start
```

For PowerShell, run `./community.ps1 start`. Review scripts before running them. No remote shell pipeline is required.

## Build a Docker image from source

Install and start Docker, then clone the source and run the build from the repository root, where `Dockerfile` and `requirements.txt` are located:

```sh
git clone https://github.com/cat-proof/catproof-community.git
cd catproof-community
docker build -t catproof-community:local .
docker images catproof-community
```

The final `.` selects the current directory as the build context. This builds and tags the application image only. It does not start CatProof, PostgreSQL or the HTTPS proxy.

To use that image with the complete local stack, initialize the configuration and select the image explicitly:

```sh
./community.sh init
CATPROOF_IMAGE=catproof-community:local docker compose up -d --no-build --wait
```

Windows PowerShell:

```powershell
.\community.ps1 init
$env:CATPROOF_IMAGE = 'catproof-community:local'
docker compose up -d --no-build --wait
```

After startup, complete the [local HTTPS trust step](#trust-local-https) before browser sign-in. For future Compose operations on this installation, keep selecting the same image with `CATPROOF_IMAGE`. The default launcher pulls the published release image unless you use its source-build option.

### Build and run in one step

The launcher can build the application and start all required services together:

```sh
./community.sh start --build
```

On Windows PowerShell:

```powershell
.\community.ps1 start -Build
```

This also generates local configuration on first use and preserves it on reruns. No manual `docker build` command is needed for this path.

### Pull the published image instead

```sh
docker pull ghcr.io/cat-proof/catproof-community:0.2.0
```

Pulling downloads the application image only. Use `./community.sh start` or `.\community.ps1 start` to configure and run the complete stack with the published image.

## First start

The launcher creates `.env` once using Python inside an upstream container. It generates a random database password, a disabled-bootstrap password and a connector encryption key. It does not print the values or replace an existing `.env`. Keep that file private and back it up with your database.

To build the application yourself, use `./community.sh start --build` or `./community.ps1 start -Build`. Startup runs database migrations before accepting traffic. Start with an empty installation for the first release; see the upgrade instructions before using existing data.

## Trust local HTTPS

Export the local Caddy root certificate:

```sh
./community.sh certificate
```

On PowerShell use `./community.ps1 certificate`. This writes `catproof-local-ca.crt`, a public certificate. Manually import and trust this certificate using your OS or browser's certificate settings, then reload https://localhost:8443/signup. Do not disable browser TLS validation. The launcher does not modify the host trust store.

On systems with curl, verify without changing the trust store:

```sh
curl --cacert catproof-local-ca.crt https://localhost:8443/healthz
```

Use `curl.exe` for that command in Windows PowerShell. The expected response is `ok`.

## Create a local account

Open https://localhost:8443/signup. Create a personal account, name your registry and add your first agent. These credentials belong to your local installation. A CatProof Cloud login is not needed. Automatic outbound email is not configured in the bundle, so keep account credentials safe.

Preview the fictional demo at https://localhost:8443/demo. Demo records are separate from your real registry. Source discovery requires an authorized connection configured in the app; see the included `guides/` directory and [published connector scopes](https://catproof.ai/connectors/).

## Manage connection slots

Community Edition allows two saved, unarchived source connections per registry. Every connector type uses the same quota, including peer and discovery connections. Pausing does not free a slot. Agent records, CSV imports and the separate fictional browser demo do not use source slots.

In Connected registries, an owner can choose **Archive connection** and confirm. The connection stops refreshing, its observations become stale, and its records and history remain. Archive frees a slot. **Restore connection** requires a free slot and leaves the source paused until you enable it.

If an older installation has more than two unarchived connections, only the two oldest may preview or refresh. Archive unwanted connections to choose the two you want to use.

## Stop or inspect

```sh
./community.sh status
./community.sh logs
./community.sh stop
```

Use the equivalent `./community.ps1` commands in PowerShell. Stop preserves data. Never add `-v` to `docker compose down` unless you intentionally want to erase the database and local certificate authority.

## Troubleshooting

- **Docker is unavailable:** start Docker Desktop/Engine and verify `docker compose version`.
- **Image cannot be pulled:** confirm that the release image exists, then check registry access and the release notes. Source builds remain available.
- **Port 8443 is already in use:** stop the conflicting listener. Changing the port also requires updating the application origin and proxy configuration.
- **Browser certificate warning:** complete manual trust of the local CA. Do not use a browser warning bypass as the setup procedure.
- **Credential saving fails:** preserve `.env`, check `CONNECTOR_ENCRYPTION_KEYS` is present, and inspect sanitized application logs. Never replace the key to fix an existing installation without a recovery plan.
- **Private endpoint rejected:** hosted egress protections remain enabled in Community Edition. Private-address provider endpoints and redirects are rejected. Do not weaken network protections to connect fixtures.
