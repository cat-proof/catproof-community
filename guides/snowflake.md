# Connect Snowflake Cortex Agents

## 1. Prepare your account

Use a Snowflake account you are authorized to inventory. Identify the account HTTPS origin, database and schema containing existing Cortex Agents. Example scope: database ANALYTICS, schema PUBLIC. Each CatProof connection scans one schema.

Ask your Snowflake administrator for a reader role with USAGE on the parent database/schema and an appropriate existing access privilege on the chosen agents. Snowflake's SHOW visibility rules include MONITOR, USAGE, MODIFY or OWNERSHIP on an agent. Do not use ACCOUNTADMIN for the connector. MONITOR is read-oriented but can expose logs/traces; CatProof only requests inventory metadata, not traces. Have the administrator approve the role and network/authentication policy.

Verify the same role can see the expected agents in Snowflake:

```sql
USE ROLE CATPROOF_READER;
SHOW AGENTS IN SCHEMA ANALYTICS.PUBLIC;
```

## 2. Prepare authentication

Generate a role-restricted PAT in Snowsight or obtain an approved OAuth access token. Use the read permissions described above. Password authentication is not supported.

Enter the credential directly in the CatProof connection form. It is encrypted, scoped to this connection and never shown again. Use Update credentials on the saved connection to replace expired or rotated tokens. This release does not perform OAuth sign-in or automatic token renewal.

## 3. Configure CatProof

Open Connector catalog → Snowflake Cortex Agents → Configure connector.

- Registry name: Snowflake · Analytics
- Registry origin: https://YOUR-ACCOUNT.snowflakecomputing.com
- Database: ANALYTICS
- Schema: PUBLIC
- Active role: CATPROOF_READER, or leave empty to use the token/default role context
- Authentication: enter the provider credentials directly in CatProof

Save the connection. CatProof sends the optional role as X-Snowflake-Role and reads the Cortex Agent list endpoint. It does not run agents, queries, tools or warehouses.

## 4. Verify

In Federated registries, click Check setup, then Sync this registry. The offline check should confirm credentials are configured. A successful sync shows LIVE and an observed count. Compare source IDs/names with SHOW AGENTS using the same role. Source ownership is a Snowflake role, not a verified business owner; assign the business owner locally in the canonical record.

## Troubleshoot

- HTTP 401: expired/incorrect token, or authentication/network policy rejected the credential.
- HTTP 403: role or object visibility is insufficient; confirm token role restriction and active role.
- Zero agents: verify account, exact database/schema spelling and same-role visibility. Zero does not establish account-wide absence.
- Partial/invalid pagination: CatProof fails the refresh and retains the prior snapshot instead of applying incomplete data.

## References

https://docs.snowflake.com/en/user-guide/programmatic-access-tokens
https://docs.snowflake.com/en/sql-reference/sql/show-agents
https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-rest-api
https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-setup
