# Connect securely in CatProof

1. Open Connector catalog, select a provider and Configure connector.
2. Enter the authorized account origin and scope (project, region, schema or workspace).
3. Choose Authentication and enter credentials directly in the secure fields. Use read-only permissions.
4. Select Connect & test. Check the sync result: saving a credential alone does not prove access.
5. Use Update credentials on the saved connection to replace secrets, or Remove saved credentials & pause connection.

No hosting account or deployment changes are required. Values are never shown again. Updates replace the complete credential set. Removal pauses discovery, retains evidence and blocks fallback to older operator-configured credentials for this connection. Revoke the credential at the provider if it should no longer work anywhere.

## Supported authentication

- Databricks, Snowflake, Lovable and LangSmith: provider access token or API key.
- Microsoft Copilot Studio: delegated Power Platform token. App-only and Graph tokens are unsupported.
- Microsoft 365 and Entra: Graph access token with the catalog's read permissions.
- Microsoft Foundry: Entra access token for ai.azure.com.
- Google Registry and Runtime: Google OAuth access token.
- Salesforce: Salesforce OAuth access token.
- ServiceNow: bearer token, or username and password.
- AWS Bedrock, AgentCore and Registry: access key ID + secret access key, plus session token for temporary credentials. Prefer temporary credentials. Hosted connections cannot use a profile on your laptop.
- A2A, MCP and peer/discovery registries: public access, or a bearer token for protected endpoints.
- Local Codex and Claude Code: Local devices enrollment using a one-time code and revocable report-only device credential.

OAuth access tokens are accepted, but provider OAuth sign-in, refresh-token storage and automatic renewal are not implemented. Replace expired tokens using Update credentials.

## Protection and limits

Only registry owners can save, replace or remove credentials. The server validates the session, request origin, membership, connection ownership and allowed credential fields. Authenticated encryption binds stored credentials to both registry and connection in a separate PostgreSQL table. The encryption key is held separately in CatProof service configuration. Secret values are omitted from inventory, exports, registry snapshots and audit details. Browser fields are masked and cleared after submission, closing or hiding the tab. No browser storage is used. HTTPS protects the hosted connection.

Credentials are decrypted in server memory for requests to the configured provider. This is not zero-knowledge storage or a managed HSM/KMS vault. Operators with both the database and encryption key can decrypt credentials. Connect only to authorized endpoints, use limited read-only access and revoke secrets at the provider when needed.
