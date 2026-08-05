# ADR-0001: Manual peer-to-peer recipe synchronization

## Status
Accepted

## Date
2026-08-04

## Context
EPN Recipe Box is a small Flask application with server-rendered HTML, one SQLite file per installation, and no external service. Users want installations on a LAN or manually connected private network to exchange recipes. The first release must preserve existing data, avoid paid infrastructure, and remain deployable on the existing Raspberry Pi service.

## Decision
Use a manual, pull-based peer model over the existing Flask server. An operator registers a peer URL and shared token, previews remote changes, then explicitly runs an import. The local installation exposes a token-authenticated manifest and recipe endpoint. Recipe IDs remain stable across installations; a deterministic content checksum and per-peer baseline distinguish unchanged, one-sided, and two-sided changes.

Schema changes are additive. Before the first migration against an existing database, create a timestamped SQLite backup in the same data directory. No deletion tombstones are added in this version, so local deletion never propagates.

## Alternatives considered

### Central synchronization service
Rejected: adds operational cost, a new trust boundary, and a paid/proprietary-service risk.

### Filesystem or shared SQLite database
Rejected: unsafe across hosts and couples installations to storage layout.

### Automatic last-write-wins without preview
Rejected: can silently destroy a user's local recipe and does not satisfy conflict safety.

### New synchronization daemon
Rejected: unnecessary new service and firewall surface; the existing Flask host is sufficient.

## Consequences
- LAN/Tailscale users must configure the peer URL and token manually.
- Sync is explicit and observable, not automatic.
- Recipe cards synchronize, while ratings/comments/users remain local.
- Conflicts require a user decision and are never silently overwritten.
- Exposing the endpoint outside a trusted network requires HTTPS/reverse proxy protection and careful token handling; the app does not open firewall ports.
- The local SQLite file contains peer configuration, so filesystem permissions remain important.
