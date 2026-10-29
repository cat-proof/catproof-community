# Connect an A2A Agent Card

## 1. Identify an authorized endpoint

Obtain the HTTPS origin and Agent Card path from the team operating the agent. The default path is /.well-known/agent-card.json. A CatProof connection reads one supplied card, not an entire network or organization. There is no network scanning or agent invocation.

The card must include a name, a skills array and an advertised endpoint through its v0.3 url or v1 supportedInterfaces. CatProof preserves capabilities and skills as source evidence. It does not infer a business owner, verify a card signature or certify that advertised capabilities work.

## 2. Prepare authentication

Select Public endpoint · no authentication for a public card. For a protected card that accepts bearer authentication, obtain a read credential from the operator and select API key / bearer token. Runtime authentication may differ from card authentication.

Enter the credential directly in the CatProof connection form. It is encrypted, scoped to this connection and never shown again. Use Update credentials on the saved connection to replace expired or rotated tokens. This release does not perform OAuth sign-in or automatic token renewal.

## 3. Configure CatProof

Open Connector catalog → A2A Agent Card → Configure connector.

- Registry name: A2A · Customer support
- Registry origin: https://YOUR-AGENT-HOST
- Agent Card path: /.well-known/agent-card.json, or the operator-supplied local absolute path
- Authentication: enter the provider credentials directly in CatProof

The origin must not include paths, query parameters or credentials. Enter the card path in its separate field. Save and Check setup, then Sync this registry. The only upstream operation is an HTTP GET of the card.

## 4. Verify with a local example

The standalone localhost registry can use this fixture without a vendor account. Initialize its PostgreSQL database and launch `federation_app.py` as described in [the federation guide](../FEDERATION.md). The hosted/Compose app blocks private-address provider endpoints; use the isolated integration suite there rather than weakening egress checks:

```sh
python3 fixture_registry.py --port 8772
```

Create an A2A connection with origin http://127.0.0.1:8772, the default path and Public endpoint authentication. Sync, then enable Include seeded demo records. Open Demo · A2A policy assistant and inspect its advertised skill and source identity. It remains labeled seeded because the fixture explicitly identifies itself as demo data.

For a real card, compare its advertised endpoint, name/version and skills with the operating team's expected metadata. Link the identity to an existing canonical agent only when you have evidence they are the same logical agent.

## Troubleshoot

- HTTP 401/403: card authentication or visibility is missing; do not assume the runtime token is valid for the card.
- HTTP 404: confirm the operator's card path.
- Invalid Agent Card: ensure it has name, skills and endpoint/interface metadata.
- Redirect rejected: configure the final authorized origin/path explicitly. Credentials are not forwarded across origins.
- Public card works but the agent fails at runtime: runtime health is outside this discovery connector.

## Reference

https://a2a-protocol.org/dev/specification/agent-card/
