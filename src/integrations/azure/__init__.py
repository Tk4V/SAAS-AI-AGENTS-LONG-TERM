"""Azure integration — auth via az CLI service principal.

Credentials (client_id, client_secret, tenant_id, subscription_id) are
stored as a BEARER JSON credential and passed to the in-process
``clyde_azure`` MCP server at session start. No OAuth flow is used.
"""
