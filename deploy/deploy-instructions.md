# Deploying Personal Expenses to a home server

The app runs as the `personal-expenses` container in the main compose file on the server, at http://&lt;server&gt;:8081.
The Windows share set in `DEPLOY_SHARE` (local `.env`, e.g. `\\<server>\Docker`) maps to `<docker-config-dir>` on the server.

## Pushing an update

1. On the PC, run the tests:
   ```powershell
   cd c:\Projects\personal-expenses; uv run pytest -q
   ```
2. If Gemini settings changed in the local `.env`, copy them to the server (see [Server settings](#server-settings)):
   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\sync_server_env.ps1
   ```
3. Build the image and copy it to the share. This writes `<DEPLOY_SHARE>\personal-expenses\container_files\personal-expenses-<timestamp>.tar` and refreshes `update_personal_expenses.sh`:
   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\publish.ps1
   ```
4. On the server, load the image and recreate the container:
   ```bash
   ssh <user>@<server>
   bash <docker-config-dir>/personal-expenses/update_personal_expenses.sh
   ```
   The script loads the newest tar, tags it `personal-expenses:latest`, deletes older tars, runs `docker compose up -d --force-recreate personal-expenses`, and waits for the container to report healthy. Once it is healthy, the script removes every older `personal-expenses` image from the server. If the health check fails, it leaves the older images in place.
   Alembic migrations run automatically when the container starts (`docker-entrypoint.sh`).

Check the deployment with `docker logs -f personal-expenses`, or open http://&lt;server&gt;:8081.

## Server settings

The `personal-expenses` service is defined in `<docker-config-dir>/docker-compose.yaml`. Its settings come from the shared root `.env` in the same folder, through compose variable substitution. That file is used by other services too, so edit only the keys this app needs:

| Key | Purpose |
|-----|---------|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Production database |
| `ENV_MODE`, `APP_PORT` | `PROD`; port `8081` |
| `GEMINI_API_KEY`, `GEMINI_MODEL_NAME` | EOB parsing; synced from the local `.env` by `deploy/sync_server_env.ps1` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_SSL`, `CLAIM_EMAIL_TO`, `CLAIM_EMAIL_CC`, `CLAIM_EMAIL_BCC` | Insurance claim emails; sync with `deploy/sync_server_env.ps1 -Keys SMTP_HOST,SMTP_PORT,SMTP_USER,SMTP_PASSWORD,SMTP_FROM,SMTP_USE_SSL,CLAIM_EMAIL_TO,CLAIM_EMAIL_CC` |

`sync_server_env.ps1` changes only the keys passed in `-Keys` (default: the Gemini keys) and saves a backup of the old file to `<DEPLOY_SHARE>\_backups` first. Every key must be set in the local `.env`, so leave out keys you have not set (e.g. `CLAIM_EMAIL_BCC`).

`.env` changes take effect only after the container is recreated (step 4, or `docker compose up -d --force-recreate personal-expenses` from `<docker-config-dir>`). A plain `docker restart` does not reload them.

## Files

| Repo path | Server path (`<docker-config-dir>/personal-expenses/`) |
|-----------|-------|
| `deploy/publish.ps1` | Writes the image tar to `container_files/` |
| `deploy/update_personal_expenses.sh` | Copied to `update_personal_expenses.sh` by `publish.ps1` (LF line endings) |
| `deploy/sync_server_env.ps1` | Updates the Gemini keys in `../.env` |

## Notes

- `publish.ps1` saves the image locally and then copies it, because `docker save` written straight to the SMB share produces an empty file.
- Images are built for `linux/amd64`.
- After a successful deploy, the update script keeps only the newest tar and the newest image. To roll back, rebuild the older commit and publish it again. If a deploy fails its health check, the older images stay on the server. You can list them with `docker images personal-expenses`, then run `docker tag personal-expenses:<old-timestamp> personal-expenses:latest` and recreate the container.
