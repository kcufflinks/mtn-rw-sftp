# MTN RW Daily SFTP Upload

A Python script that exports data from Zoho Analytics, uploads the CSV to an SFTP server, and posts a **Google Chat** notification on success with enough detail to debug server, path, and file-size issues.

## Prerequisites

- Python 3.10+
- VPN access to reach the SFTP server (`SFTP_HOST:SFTP_PORT` in `.env`)
- Zoho Analytics API credentials (see setup below)
- A Google Chat space webhook URL

## One-time Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy the example file and fill in your credentials:

```bash
cp .env.example .env
```

Then edit `.env` with your values.

| Variable | Purpose |
|----------|---------|
| `ZOHO_*` | OAuth client, refresh token, org/workspace IDs, and two export SQL queries |
| `SFTP_HOST`, `SFTP_PORT`, `SFTP_USERNAME`, `SFTP_PASSWORD` | SFTP endpoint and credentials |
| `SFTP_REMOTE_DIR` | Subfolder under the SFTP account’s login directory (see below) |
| `GCHAT_WEBHOOK_URL` | Incoming webhook for success notifications |

### 3. Set up Zoho Analytics API credentials

1. Go to [Zoho API Console](https://api-console.zoho.com)
2. Click **Add Client** → **Self Client**
3. Give it a name (e.g., "MTN RW SFTP Script")
4. Copy the **Client ID** and **Client Secret** to your `.env` file
5. Click **Generate Code** with scope: `ZohoAnalytics.data.read` (pick `offline` / refresh-capable if the console offers it so the exchange returns a `refresh_token`).
6. **Immediately** exchange that **one-time code** (it expires in a few minutes) for tokens. Do **not** paste the grant code into `ZOHO_REFRESH_TOKEN` in `.env`—that is what causes the `invalid_code` error when the script runs.

   Use the same `accounts` host as your `ZOHO_DATA_CENTER` (example below is US; use `accounts.zoho.eu` + EU console for EU, etc.):

```bash
curl -s -X POST "https://accounts.zoho.com/oauth/v2/token" \
  -d "grant_type=authorization_code" \
  -d "client_id=YOUR_CLIENT_ID" \
  -d "client_secret=YOUR_CLIENT_SECRET" \
  -d "code=PASTE_THE_GRANT_CODE_HERE"
```

7. From the **JSON** response, copy the value of **`refresh_token`** (a long string, often starting with `1000.`) into `ZOHO_REFRESH_TOKEN` in your `.env`. If the response has no `refresh_token`, regenerate the code and repeat—codes expire quickly and `access_type=offline` is required for a refresh token on some flows.

The refresh token is long-lived. The script uses it to get a fresh access token on each run.

**Region (if token requests fail):** Zoho splits accounts by region (US `.com`, EU, India, etc.). If you see errors when obtaining the access token, set `ZOHO_DATA_CENTER` in `.env` to match your org (for example `eu` or `in`). The API Console you use must match the same region (e.g. [api-console.zoho.eu](https://api-console.zoho.eu) for EU). When exchanging the authorization code for a refresh token, the `curl` URL must use the same accounts host (e.g. `https://accounts.zoho.eu/oauth/v2/token` for EU), not only `accounts.zoho.com`.

### 4. Find your Zoho Org ID and Workspace ID

You can find these in the Zoho Analytics URL when viewing your workspace:

`https://analytics.zoho.com/workspace/WORKSPACE_ID/...`

Or use the Zoho Analytics API to list organizations and workspaces.

### 5. Configure the SQL queries

Set `ZOHO_SQL_QUERY` and `ZOHO_SQL_QUERY_2` in your `.env` to the SQL queries that export your data. Both run on every upload.

```
ZOHO_SQL_QUERY=select * from "YourTableName"
ZOHO_SQL_QUERY_2=select * from "YourOtherTableName"
```

Files are saved and uploaded as `mtn_rw_contracts_YYYY_MM_DD-YYYY_MM_DD.csv` (from `ZOHO_SQL_QUERY`) and `mtn_rw_payins_YYYY_MM_DD-YYYY_MM_DD.csv` (from `ZOHO_SQL_QUERY_2`) by default. The date range is today and the previous 2 days (UTC). Override with `ZOHO_EXPORT_1_FILENAME_PREFIX` / `ZOHO_EXPORT_2_FILENAME_PREFIX` if needed.

### 6. Set up Google Chat webhook

1. Open the Google Chat **space** that should receive run notifications.
2. **Apps & integrations** → **Webhooks** → **Add webhook**.
3. Name it (e.g. "MTN RW SFTP") and copy the webhook URL into `GCHAT_WEBHOOK_URL` in `.env`.

**Security:** Anyone with the webhook URL can post to that space. Treat it like a secret; do not commit `.env`.

## Daily Usage

1. Connect to the VPN
2. Run the script:

```bash
python main.py
```

The script will:

1. Verify TCP connectivity to `SFTP_HOST:SFTP_PORT`
2. Obtain a Zoho access token and create two bulk export jobs
3. Poll until each export completes
4. Save the CSVs to `./exports/mtn_rw_contracts_YYYY_MM_DD-YYYY_MM_DD.csv` and `./exports/mtn_rw_payins_YYYY_MM_DD-YYYY_MM_DD.csv` (same date range: today and previous 2 days)
5. Upload both files to the SFTP server in one session
6. Post a **success** message to Google Chat

On failure, the script prints errors to the terminal and exits with code `1`. **Failure notifications to Chat are not implemented yet**—only successful runs post to the webhook.


| Field | Meaning |
|-------|---------|
| **Server / User** | Which SFTP endpoint and account were used |
| **Configured dir** | `SFTP_REMOTE_DIR` from `.env`, or `(not set)` |
| **Session directory** | SFTP working directory after login (`getcwd`), or `(not reported by server)` if the host returns `None` |
| **Remote file** | Path passed to `put()` for each uploaded file |
| **Resolved** | Full path when `getcwd` is available; otherwise the remote file path marked as relative |
| **Size** | Local vs remote bytes per file; ✓ or ⚠️ mismatch |
| **Fallback path used** | `yes` if upload used `upload/...` instead of the primary path |
| **Zoho job** | Export job ID per file for Zoho support |
| **Duration** | Wall time for the whole run |

Use **Server**, **User**, **Session directory**, and **Resolved** when coordinating with your SFTP administrator about folder location.

## Testing locally

The script is end-to-end: it needs a working VPN to the SFTP host (if applicable), valid Zoho credentials, and a valid Chat webhook.

1. **Create a virtual environment (optional but recommended)**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Use a real `.env`** with all required variables filled. Use a **test Chat space** webhook for first runs if you do not want production alerts.

3. **Connect the VPN** so your `SFTP_HOST:SFTP_PORT` is reachable. The first step is a TCP check; if that fails, fix VPN before debugging anything else.

4. **Run**

   ```bash
   python main.py
   ```

5. **Verify:** Check `./exports/` for both CSVs, confirm the files on the SFTP server under `SFTP_REMOTE_DIR`, and check the Google Chat space for the success message.

You cannot fully test the SFTP step without network access to that host. There is no separate mock mode in this project.

## Troubleshooting

### "Cannot reach SFTP server"

- Make sure you are connected to the VPN
- Confirm `SFTP_HOST` and `SFTP_PORT` in `.env` match what your administrator provided

### "Failed to get Zoho access token"
- Set `ZOHO_DATA_CENTER` to your Zoho region (`us`/`com`, `eu`, `in`, etc.); US orgs usually use `us` or `com`
- The refresh token must come from the **same** API client as `ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET` (if you recreated the client, generate a new refresh token)
- Do not paste a one-time **authorization code** where the **refresh token** belongs; you need the `refresh_token` field from the token exchange response

### "SFTP authentication failed"

- Check `SFTP_USERNAME` and `SFTP_PASSWORD` in `.env`

### SFTP upload permission denied / "no such file"

- Confirm `SFTP_REMOTE_DIR` matches what the server owner expects
- For Docker `atmoz/sftp`, try `SFTP_REMOTE_DIR=upload`

### Size mismatch in Chat or logs

- Re-run the export; if it persists, check VPN stability and SFTP server disk/quota

### Google Chat notification failed

- Verify `GCHAT_WEBHOOK_URL` is correct and the webhook was not deleted
- Confirm the Chat space still allows incoming webhooks
- A failed Chat post exits with code `1` even if the SFTP upload succeeded—fix the webhook and re-check the file on SFTP

### Run failed but nothing in Chat

- Expected today: only **successful** runs post to Google Chat. Check terminal output and exit code for failures.

### Logs show `SFTP session working directory: None` or Chat says `(not reported by server)`

- Some SFTP servers do not implement `getcwd` / realpath; Paramiko then returns `None`. Uploads can still succeed using relative paths like `{SFTP_REMOTE_DIR}/file.csv`.
- The remote directory listing in the logs confirms the folder and file on the server.
- Ask your SFTP administrator for the **absolute path** on disk for your account if you need it for tickets; the script cannot infer it when `getcwd` is missing.
