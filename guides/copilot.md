# Connect Microsoft Copilot Studio / Agent Builder

## 1. Prepare the tenant

Have your Power Platform administrator confirm that tenant inventory is available/enabled and the signed-in user has access to the required agent inventory. Use the authorized tenant and an appropriate Power Platform administrator role. This connector reads Copilot Studio/Agent Builder inventory; it does not inventory Microsoft Foundry, the separate Microsoft 365 catalog or original Power Virtual Agents V1 agents.

Important: the current Power Platform inventory query API requires delegated user authentication. Service-principal/client-credentials and managed-identity tokens are not supported and return HTTP 403. A Microsoft Graph token is also the wrong audience.

## 2. Prepare authentication

Obtain a delegated user access token for https://api.powerplatform.com through your organization-approved Microsoft authentication process. Complete MFA and consent normally. App-only, client-secret and Microsoft Graph tokens are not supported.

Enter the credential directly in the CatProof connection form. It is encrypted, scoped to this connection and never shown again. Use Update credentials on the saved connection to replace expired or rotated tokens. This release does not perform OAuth sign-in or automatic token renewal.

## 3. Configure CatProof

Open Connector catalog → Microsoft Copilot Studio / Agent Builder → Configure connector.

- Registry name: Copilot · Main tenant
- Registry origin: https://api.powerplatform.com
- Authentication: enter the provider credentials directly in CatProof
- Tenant ID: the UUID of the authorized Microsoft tenant (required for a new connection)

Save, then Check setup. The adapter issues a read-only POST resource query restricted to microsoft.copilotstudio/agents and follows skip tokens. It does not create, publish, modify, block or invoke an agent.

## 4. Verify

Click Sync this registry. Compare results with Power Platform admin center → Manage → Copilot Studio using the same tenant/user. Both draft and published agents may appear. Publication time, environment and applicable Entra IDs are source evidence; preview fields may be missing. Assign accountable business ownership locally rather than inferring it from identity references.

## Troubleshoot

- HTTP 401: refresh the access token; verify the Power Platform audience.
- HTTP 403: first check whether the token is app-only, then tenant role, consent, inventory availability and conditional-access requirements.
- Zero agents: verify tenant/user and environment visibility in the admin center.
- Truncated response without a continuation token: the connector rejects it and retains prior records.

## References

https://learn.microsoft.com/en-us/power-platform/admin/inventory-api
https://learn.microsoft.com/en-us/power-platform/admin/programmability-authentication-v2
https://learn.microsoft.com/en-us/microsoft-copilot-studio/admin-agent-inventory
