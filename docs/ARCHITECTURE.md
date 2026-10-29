# Architecture and data handling

The bundle runs three services: Caddy provides local HTTPS on loopback, the Python application manages authenticated registries, and PostgreSQL stores accounts, inventory, credentials and history. Only the proxy publishes a host port. The app runs as an unprivileged container user.

Canonical agent records hold the business purpose, owner, lifecycle and review decisions. Source observations retain vendor IDs, scope, provenance and freshness. Operators explicitly link identities. Refreshes do not overwrite canonical decisions. Failed reads retain previous observations as stale evidence.

Connector credentials are encrypted at rest with the key held in `.env`, then decrypted in server memory for authorized provider reads. Anyone with both the database and the key can decrypt them. This is application-managed encryption, not zero-knowledge storage or a managed hardware vault.

Provider access remains read-only. Network protections reject private-address provider endpoints and redirects. Local devices report scoped metadata using separately enrolled credentials. A source record marked active or an agent marked suspended in the registry is not evidence of runtime health or enforcement.

The software contains no production accounts, credentials, customer data, or database volumes. Test fixtures and public demonstrations are fictional. Runtime state is not included in source or image releases. This repository starts with a clean source snapshot rather than private development history.

This is an early-access release. Email identity verification, enterprise SSO/MFA and immutable audit storage are not supplied by this bundle. Local HTTP, authentication and PostgreSQL checks do not establish real-provider compatibility or production readiness.
