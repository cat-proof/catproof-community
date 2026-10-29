# Connect Google Agent Registry

## 1. Prepare a project and location

Select the authorized Google Cloud governance project and location containing the registered agents. Ask the administrator to ensure Agent Registry API (agentregistry.googleapis.com) is enabled and your caller has agentregistry.agents.list. The predefined roles/agentregistry.viewer role provides read-only registry access, including that permission. An administrator can grant that role on the intended project according to your organization's policy.

This is project/location-scoped discovery. Automatic registration does not guarantee that every agent in other projects or unsupported runtimes is represented. Confirm the expected scope with the platform owner.

## 2. Prepare authentication

Obtain a Google OAuth access token through your approved Google authentication or workload identity process. A browser ID token or service-account JSON key is not supported. CatProof does not grant impersonation access.

Enter the credential directly in the CatProof connection form. It is encrypted, scoped to this connection and never shown again. Use Update credentials on the saved connection to replace expired or rotated tokens. This release does not perform OAuth sign-in or automatic token renewal.

## 3. Configure CatProof

Open Connector catalog → Google Agent Registry → Configure connector.

- Registry name: Google · Governance project
- Registry origin: https://agentregistry.googleapis.com
- Project ID: the governance project ID, not a display name
- Location: the registry's configured location, such as global or us-central1; use the location your operator confirms
- Quota/billing project: optional; use the approved quota project if your credential requires one
- Authentication: enter the provider credentials directly in CatProof

Choose Connect & test. The optional quota project is sent as X-Goog-User-Project. The caller needs the applicable service-usage permission on that quota project when required; it does not replace read access to the resource project.

## 4. Verify

Compare returned source resource names with the Agent Registry console in the same project/location. Source IDs retain the full project/location-qualified resource name. Repeated sync should preserve the canonical ID and any locally assigned owner/lifecycle. Missing source metadata remains unknown.

For direct Agent Runtime/Agent Engine discovery, Show all connectors → Google Agent Runtime / Agent Engine. Use its regional https://LOCATION-aiplatform.googleapis.com origin, project/location and a caller with the corresponding Vertex AI reasoning-engine list permission. This is a separate adapter and identity surface; link registry/runtime records explicitly rather than deduplicating by name.

## Troubleshoot

- HTTP 401: expired token or wrong token type; obtain a fresh OAuth access token.
- HTTP 403: verify API enablement, agentregistry.agents.list and the selected quota-project permissions.
- HTTP 404 or zero agents: check project ID and exact registry location, then compare console visibility under the same identity.
- Source returns an unreachable/incomplete scope: CatProof rejects the refresh and retains prior observations.
- An expected runtime is absent: check whether it has actually been registered, or use the separate direct Runtime adapter.

## References

https://docs.cloud.google.com/agent-registry/reference/rest/v1/projects.locations.agents/list
https://docs.cloud.google.com/agent-registry/roles-permissions
https://docs.cloud.google.com/agent-registry/register-agents
