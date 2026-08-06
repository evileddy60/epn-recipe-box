# Migration plan: checkout to release layout

This plan is prepared only; it has not been executed.

1. Verify the current service and Tailscale health without changing it.
2. Create and verify a fresh online backup of the database, uploads, and recipe images.
3. Build a release from known-good commit `218521d60b51c07ffd5f44c7545700517e93788e` and verify it locally.
4. Transfer and stage it into `/opt/epn-recipe-box/releases/<release-id>`.
5. Create `/opt/epn-recipe-box/current` only after the staged release passes verification.
6. Install the revised unit atomically, preserving the external environment file and loopback bind, then reload systemd and restart during the approved change window.
7. Verify local health, schema, Tailscale health, and persistence after a controlled restart.
8. Retain the old `/opt/epn-recipe-box` checkout and its rollback path until the release model is proven.
9. Archive or remove the checkout only in a later explicitly approved task.

Prerequisites: confirm the actual environment file used by the current service, confirm the service-user can read the release and shared venv and write only shared data, confirm migration commands and schema 14, and test the privileged installer/restore contract. No step authorizes production mutation in the design task.
