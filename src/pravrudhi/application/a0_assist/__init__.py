"""A0 screening assistance (pravrudhi-app#125): the engine side of the assisted screening route.

Modules: `config` (fail-closed reading of configs/assistant_tier.yaml), `store` (the spend and session store: the narrow
Postgres role, optional `pg8000`), `transport` (the Anthropic Managed Agents HTTP
client with a path allowlist), `limits` (in-memory anti-abuse limits). Nothing here is reachable from a route until PR-B
wires it, and the assisted route ships dark (`assist_route_enabled: false`).
"""
