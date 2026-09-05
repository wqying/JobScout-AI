# ADR 0001: Local network boundary

Status: accepted for V1

JobScout has no authentication in V1. Compose therefore publishes web, API, PostgreSQL, and Redis
only on `127.0.0.1`. Containers listen on their internal bridge interfaces so sibling containers can
communicate; the host-side publication remains loopback-only. Running the API directly refuses a
non-loopback `API_HOST` or `WEB_HOST` unless `ALLOW_REMOTE_ACCESS=true` is explicitly set.

The flag does not add authentication. Public hosting requires a future security ADR and is outside
V1 scope.
