# Password-reset Gmail staging

This document describes the bounded staging test. It is not a deployment procedure.

## Credential contract

- SOURCE_CREDENTIAL_OWNER: epn (current local OAuth files are owned by the Hermes operator account)
- TARGET_SERVICE_USER: epn-recipe-box
- PROPOSED_SECURE_MOUNT: `/run/epn-recipe-box/secrets/google/`, provided by the host secret-management mechanism
- PROPOSED_PERMISSIONS: directory 0750 owned by root:epn-recipe-box; token and client files 0640 owned by root:epn-recipe-box; no project-tree copy and no world/group-readable files beyond the service group
- CREDENTIAL_ROTATION_IMPACT: restart or reload the Recipe Box service after atomically replacing the mounted files; never edit the application tree or environment with secret contents

The existing local files under `/home/epn/.config/ai-work-facilitator/` are not to be chmod'ed broadly or mounted directly into production. The mount must be approved and created separately.

## Future bounded test

1. Create `/tmp/epn-recipe-box-password-reset-staging/` and set `EPN_DATA_DIR` to it. Refuse to run if `EPN_DATA_DIR` is `/var/lib/epn-recipe-box`.
2. Use a temporary staging account and apply migrations only to the temporary SQLite database.
3. Set `EPN_MAIL_BACKEND=gmail_api`, `EPN_MAIL_ENABLED=1`, and point the two Gmail file variables at the approved staging mount.
4. Trigger exactly one password-reset request for `evileddy60@hotmail.com`.
5. Assert one provider send result and retain only provider message ID, thread ID, recipient, sender, and subject.
6. Verify the message body contains one six-digit code and one reset link, but do not write the code or link to logs. Staging may use `http://localhost`; production must set `EPN_PUBLIC_BASE_URL` to an HTTPS public origin.
7. Verify the database contains only code/web-token digests and no raw values.
8. Search the bounded application log for the code and reset token; require no matches.
9. Read back provider metadata for the exact message ID, then remove only the temporary staging directory.

The first local harness attempt exposed a test-harness defect: the Gmail adapter returns provider metadata as a message-id string, while the capture code attempted to parse that return value as a MIME message. The provider request itself was bounded and successful; the corrected harness treats the adapter return value as metadata and parses the separately captured MIME payload for content assertions. The adapter was not retried and no additional live email was sent.
