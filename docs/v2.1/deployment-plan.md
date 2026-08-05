# EPN Recipe Box 2.1 Raspberry Pi / Tailscale Deployment Plan

## Safety boundary

This phase does not deploy, enable systemd, change Tailscale ACLs, open UFW/router ports, or touch production data. Commands below are instructions for a human operator after review.

## Recommended topology

```text
Android device -- private tailnet + HTTPS --> Tailscale Serve --> 127.0.0.1:5000 Gunicorn --> SQLite/data
```

Bind Gunicorn to `127.0.0.1:5000` when Tailscale Serve is used. This avoids exposing the application on the Pi's LAN interface. If Serve is not used, bind only to the explicitly intended private interface and document the tailnet policy before starting the service.

Use the Pi's stable Tailscale DNS name (MagicDNS hostname) as the Android server URL. Do not hard-code a tailnet IP in the application.

## Systemd design

- Dedicated unprivileged `epn-recipe-box` service account where practical.
- `WorkingDirectory` points to the deployed checkout.
- Environment file is root-readable and contains a strong `SECRET_KEY`, data directory, canonical API base URL, and explicit HTTPS mode.
- `ExecStart` uses the repository virtualenv Gunicorn entrypoint `app:app`.
- `Restart=on-failure`, private temporary directory, no-new-privileges, and read/write access limited to the data directory should be reviewed before enabling.
- Health endpoint is used for local readiness checks; do not place secrets in health responses.

## Tailscale operator steps

1. Install and authenticate Tailscale on the Pi and invited devices using the organization's normal process.
2. Confirm the Pi's MagicDNS hostname and tailnet membership.
3. Configure Tailscale Serve to forward HTTPS to the local Gunicorn port, or choose a documented private-interface alternative.
4. Confirm only invited users/devices can reach the hostname through the tailnet.
5. Remove a device from the tailnet and revoke its application API tokens when access must end.
6. Revoke the user's Recipe Box account/session credentials independently of tailnet membership.

Do not automate ACL changes. Tailnet membership is transport access; API tokens remain the application authorization layer.

## Backup

Use the existing `tools/recipe_box_backup.py` against an explicit data path, including the recipe-image sidecar. Store encrypted backups off the Pi and periodically test restore to an isolated path. Never use production paths in automated tests.

## Readiness checks

- `curl --fail https://<tailscale-hostname>/api/v1/health`
- Confirm API base URL is the same canonical HTTPS hostname shown in app setup.
- Confirm `GET /api/v1/health` reports database `ok` and the expected schema version.
- Confirm no public DNS, router forwarding, or UFW rule was added by this project.

## Rollback

Stop the new API/client access path, leave the existing web routes running, restore the previous systemd unit/environment if changed by an operator, and restore only from a verified backup if a database change has been explicitly applied. The API foundation is additive and does not require a schema downgrade.

## Sources

- Tailscale documentation: https://tailscale.com/kb
- Tailscale Serve: https://tailscale.com/kb/1242/tailscale-serve
- Tailscale DNS/MagicDNS: https://tailscale.com/kb/1081/magicdns
- Gunicorn deployment: https://docs.gunicorn.org/en/stable/deploy.html
