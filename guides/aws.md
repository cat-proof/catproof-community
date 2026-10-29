# Connect AWS Bedrock, AgentCore and Agent Registry

## 1. Choose the discovery surface

The top-five primary AWS connector is AWS Bedrock Agents. Two related adapters are available under Show all connectors:

- AWS Bedrock Agents: lists agent definitions with bedrock:ListAgents.
- AWS AgentCore Runtime: lists deployed runtimes with bedrock-agentcore:ListAgentRuntimes.
- AWS Agent Registry: lists records in one configured registry with agent-registry:ListRegistryRecords. It uses the current agent-registry-control SDK namespace, not the retired preview registry namespace.

These are separate source identities. Link them explicitly when they represent the same business agent. Registry approval, definition preparation and execution health are different facts.

## 2. Prepare read permissions

Use a customer-authorized AWS account and region. Ask the administrator for the required list action above, scoped according to the applicable AWS service authorization rules. Do not grant invoke, create, update or delete actions for this connector. You do not need all three adapters' permissions when using only one adapter.

For Bedrock list-only discovery, a minimal policy shape is:

```json
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"bedrock:ListAgents","Resource":"*"}]}
```

The administrator must review and attach this to the intended identity. Account SCPs, permission boundaries and region restrictions still apply. CatProof does not create IAM users or grant permissions.

## 3. Prepare read-only credentials

Obtain an approved AWS access key ID, secret access key and session token when issued. Prefer temporary credentials. Enter the complete set directly in CatProof. Credentials are encrypted and never displayed again. Use Update credentials when they expire. CatProof does not create IAM identities, assume roles or refresh credentials automatically. A profile on your laptop is not available to the hosted service.

## 4. Configure CatProof

Open Connector catalog → AWS Bedrock Agents → Configure connector.

- Registry name: AWS · Production · us-east-1
- Registry origin: keep the AWS example origin; the SDK derives actual service endpoints from the region
- AWS region: your configured region, for example us-east-1
- Authentication: enter the provider credentials directly in CatProof
- AWS account ID: the authorized 12-digit account ID (required for new Bedrock and AgentCore connections)

For the AWS Agent Registry adapter also supply the existing Registry ID or ARN. Each account/region and each registry scope needs its own connection. Choose Connect & test.

## 5. Verify

Compare agent IDs/counts with the matching AWS console account and region. Check setup verifies SDK availability and required credential fields, not actual AWS permission. Sync performs the live list call. AgentCore READY is source metadata, not a probe of runtime behavior. Non-agent AWS registry records appear under Tools & identities.

## Troubleshoot

- SDK unavailable: contact CatProof support.
- Session expired: obtain a new approved temporary credential set and use Update credentials.
- Access denied: confirm the correct list action, account, region and effective permission boundaries.
- Zero records: compare the exact account/region/surface. Bedrock definitions and AgentCore runtimes are not interchangeable lists.
- Old registry API errors: use the current AWS Agent Registry adapter; do not change it to the discontinued preview namespace.

## References

https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent_ListAgents.html
https://docs.aws.amazon.com/service-authorization/latest/reference/list_bedrock-agentcore.html
https://docs.aws.amazon.com/cli/latest/reference/agent-registry-control/list-registry-records.html
https://docs.aws.amazon.com/service-authorization/latest/reference/list_agent-registry.html
