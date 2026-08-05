# EPN Recipe Box 2.1 Threat Model

## Trust boundaries

1. **Android device boundary** — device storage, app process, screenshots, backups, and other apps are not fully trusted.
2. **Tailscale transport boundary** — private tailnet access reduces exposure but does not replace application authentication or authorization.
3. **Flask API boundary** — all request headers, URL values, JSON, filters, and uploaded content are untrusted.
4. **SQLite/data boundary** — the Pi filesystem is authoritative; backups and operators can access private recipes.
5. **Peer synchronization boundary** — peer installation tokens are a different credential class and must not cross into Android APIs.

## Assets

- User passwords and password hashes.
- Revocable Android API tokens.
- Recipe content, images, profile data, favorites, and archive state.
- Tailscale hostname and deployment metadata.
- SQLite database and backups.
- Peer synchronization tokens.

## Threats and controls

| Threat | Control | Verification |
|---|---|---|
| Token theft from app storage | Store only through Android Keystore-backed encrypted storage; clear on logout | Android storage tests and source review |
| Token leakage in logs/crash reports | Never log Authorization headers, request bodies containing passwords, or tokens | Log/source scan |
| Brute-force login on a Pi | Per-identifier and bounded server-side rate limiting; generic auth errors | API rate-limit tests |
| Replay of expired/revoked token | Store token hash, expiry, revocation timestamp; check on every protected request | Expiry/revocation tests |
| Cross-account recipe access | Owner/account authorization at API service boundary | Authorization tests |
| SQL injection | Parameterized queries and allowlisted sort/filter fields | Ruff/security review and tests |
| Path traversal or filesystem disclosure | Return API-relative image URLs generated from stable IDs; never accept filesystem paths | Response tests |
| Oversized JSON/request abuse | Content length and parsed-field limits; bounded pagination | Size/validation tests |
| Cleartext public exposure | Android blocks cleartext by default; server deployment binds localhost behind Tailscale Serve or explicitly private interface | Config/source review |
| Tailnet membership overreach | Invite-only tailnet process and documented device removal; no ACL automation | Deployment document |
| Browser regression | Separate API auth middleware and preserved web routes/session behavior | Existing suite |
| Peer-token confusion | Dedicated token table/prefix/lookup path for API credentials; peer sync continues using installation hash | Migration and separation tests |
| Backup leakage | No production data in tests; document encrypted/off-host backup handling | Cleanup and deployment review |

## Residual risks

- A compromised Pi account or tailnet administrator can access server data.
- Android screenshots and OS-level backups may capture private recipe content; sensitive screens use `FLAG_SECURE` where practical and the distribution guide documents the trade-off.
- Tailscale is private transport, not an authorization system. Application credentials remain mandatory.
- Rate limiting in a single-process SQLite deployment is bounded and local; multi-process deployment requires a shared limiter or reverse-proxy rate limit before public exposure.

## Security acceptance criteria

No production secret, token, signing key, Tailscale credential, or production database is created, read, committed, or printed by this phase.
