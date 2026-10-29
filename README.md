# CatProof Community Edition

A self-hosted system of record for AI agents. Connect authorized inventories, link source identities, assign accountable business owners, and preserve decisions as source metadata changes.

Licensed under [MIT](LICENSE). Third-party dependencies retain their [own licenses](THIRD_PARTY_NOTICES.md).

Run on your laptop with Docker. No CatProof Cloud account or AI model API key is required. Provider connections require your own authorized credentials. The optional browser demo uses fictional records and makes no provider calls.

## Quick start

Install and start Docker Desktop (Linux containers) or Docker Engine with Compose v2. Download and extract a [release](https://github.com/cat-proof/catproof-community/releases), or clone this repository. Open a terminal in the extracted directory.

macOS / Linux:

```sh
./community.sh start
```

Windows PowerShell:

```powershell
.\community.ps1 start
```

The launcher pulls the versioned application image, generates private local credentials once, and starts PostgreSQL, CatProof and a local HTTPS proxy. Open **https://localhost:8443/signup** after completing the manual local certificate trust step in [Installation](docs/INSTALL.md). Your first account and registry are local to this installation.

To build from source instead of pulling the application image:

```sh
./community.sh start --build
```

On PowerShell, use `.\community.ps1 start -Build`.

## What is included

- Canonical agent records with purpose, business owner, lifecycle and review notes.
- Explicit links between source identities and business records.
- Read-only source discovery, retained observations and freshness indicators.
- Team roles, change history, CSV import/export and local-device enrollment.
- A fictional interactive demo at `https://localhost:8443/demo`.

Matching names do not automatically establish identity. A suspended lifecycle record does not stop an agent in its provider. Connector coverage and validation depend on the selected source. This early-access release is not a compliance certification or a promise of production readiness.

## Documentation

- [Install and verify](docs/INSTALL.md)
- [Back up, restore and upgrade](docs/OPERATIONS.md)
- [Architecture and data handling](docs/ARCHITECTURE.md)
- [Contribute and run tests](CONTRIBUTING.md)
- [Report a security issue](SECURITY.md)
- [Connector scopes](https://catproof.ai/connectors/)
- [Product concepts and hosted setup](https://catproof.ai/docs/)

## Local data and network access

Agent records and encrypted connector credentials persist in your local Docker volumes. The encryption key lives in your local `.env`. The application makes outbound requests to configured providers when you preview or refresh them. Docker downloads images and dependencies during setup; browser assets may load third-party fonts. There is no community-installation telemetry added by this packaging.

The database and application ports are not published. Only `127.0.0.1:8443` is exposed. Keep the local configuration local; this Compose bundle is not an internet-facing deployment guide.

## Support

Use GitHub issues for reproducible bugs with sanitized logs. Do not include tokens, invitation codes, `.env`, database exports or private inventories. For managed hosting, visit [CatProof](https://catproof.ai/).
