# Contributing

Open an issue describing the behavior, expected result, version and a minimal reproduction. Use synthetic data and redact logs. Keep fixes small and include a regression test for changed behavior.

Initialize local configuration without starting the application:

```sh
./community.sh init
docker compose --profile test run --build --rm tests
docker compose --profile test stop test-db
```

Tests use a separate disposable PostgreSQL database, not your registry. They do not contact vendor APIs. JavaScript model checks require Node.js:

```sh
node --test test_demo_model.cjs test_graph_model.cjs test_mapping_model.cjs
```

For a full source-build and HTTPS restart smoke check, use `./community.sh start --build`, export the local certificate, and run `python3 scripts/smoke_local.py --ca catproof-local-ca.crt --restart`. The smoke test requires Python on the host and creates synthetic accounts only in the local registry. Read the script before using it.

Preserve tenant isolation, explicit identity links, read-only provider discovery, source freshness, and the separation between business decisions and source observations. Persistence changes require explicit migrations and populated-upgrade tests. Do not add telemetry, credential collection or provider write operations implicitly. Report vulnerabilities privately as described in SECURITY.md.
