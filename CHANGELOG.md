# Changelog

## 0.2.0

Community Edition now supports two saved, unarchived source connections per registry across all connector types. Paused connections count. The server enforces the limit across both connection-creation APIs and serializes concurrent creation under a database lock.

Owners can archive and restore connections while preserving records and evidence. Archived connections stop source reads and free a slot. Existing installations with more than two connections retain their data, with source reads limited to the two oldest unarchived connections until the owner archives unwanted ones.

Includes the h0008 database migration. Back up the database and preserve .env before upgrading. Existing 0.1.0 artifacts are unchanged.

## 0.1.0

Initial Community Edition packaging of the canonical agent registry: records, deliberate identity links, ownership, review decisions, configured read-only discovery and local team accounts.

Adds Docker Compose distribution, versioned application image configuration, per-installation random credentials, POSIX and PowerShell launchers, local HTTPS, operations documentation, and CI coverage. This is an early-access release. Platform checks and known limitations are recorded with each release.
