# Connect Lovable in CatProof

## Before you start

Use a Lovable Business or Enterprise workspace and create a dedicated workspace API key with **Workspace: Read** access. You need its workspace ID and API key. CatProof uses Lovable's official read-only project inventory API; it never edits, publishes or deletes projects.

## Connect

1. Open Connector catalog → Show all connectors → Lovable Projects → Configure connector.
2. Give the connection a name and enter the Lovable workspace ID.
3. Paste the API key into **Lovable workspace API key** and click **Connect & test**.
4. CatProof encrypts the key, saves the connection and reads the authorized project inventory. Successful reads open Apps, tools & identities with the connection selected.

You do not need hosting access, environment-variable names, a credential prefix, or a deployment. The key is sent over HTTPS to CatProof, encrypted with a server-managed key, scoped to your registry and connection, and never returned to the browser. Keys are not included in registry snapshots, exports or audit details. The server must decrypt a key temporarily to authenticate to Lovable; encryption does not remove the need to protect the running server and encryption key.

If this workspace is already saved, Connect & test rejects the duplicate without changing its saved key. Open the existing connection and use its credential update action to rotate the key explicitly. A failed test after an explicit credential save keeps the saved key for correction and shows the source error; it does not substitute demo projects.

## Update or remove a key

In Federated registries, open your saved Lovable connection and choose **Update API key** or **Configure API key**. Paste a new key and choose **Save & test connection**. Only registry owners can manage keys; editors can refresh and viewers can read the permitted inventory. Saved keys are never displayed. To revoke a key at its source, use Lovable's workspace access-token settings.

**Remove saved credentials & pause connection** deletes CatProof's encrypted copy and pauses future refreshes. Existing imported evidence is retained and marked stale. A non-secret removal marker prevents this connection from falling back to older operator-configured credentials. Revoke the key at Lovable to invalidate it outside CatProof.

## Where projects appear

Use **View imported projects**, or Apps, tools & identities. Projects are application assets, not automatically AI agents. Inspect source owner ID, publication status, description, source ID, last seen, workspace ID, visibility and last edit when available. Link a project to a canonical agent only when it supports that agent. Published/unpublished is not a runtime-health result; source owner IDs are not verified business owners.

Successful refreshes preserve canonical decisions and links. Missing projects become no longer observed, which does not prove deletion. Failed or incomplete refreshes retain previous evidence with stale indicators. Inventory reads can lag recent changes in Lovable.

## Troubleshooting

- API key needed: use Configure API key on the saved connection.
- 401: replace an expired, revoked or invalid key.
- 402: the Lovable workspace plan does not support its inventory API.
- 403: check Workspace: Read permission, workspace ID and any outbound-IP restriction.
- 429: wait before retrying.
- Secure credential storage unavailable: contact CatProof support; this is an operator issue, not an end-user setup step.

One connection covers one authorized workspace, bounded to 100 pages / 10,000 projects. Malformed, duplicate, cross-workspace or incomplete inventory fails the entire refresh. The adapter uses GET /v1/projects with workspace_id, limit and cursor, the Lovable-API-Key header, and API version 2026-09-11. No source code, project secrets or private application data is fetched.

References: https://docs.lovable.dev/api-reference/projects/list-projects and https://docs.lovable.dev/features/api-keys
