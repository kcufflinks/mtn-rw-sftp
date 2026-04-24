# MTN RW Daily SFTP Upload

A Python script that exports data from Zoho Analytics, uploads the CSV to an SFTP server, and sends an email notification with the file attached.

## Prerequisites

- Python 3.10+
- VPN access to reach the SFTP server (10.150.97.169:9000)
- Zoho Analytics API credentials (see setup below)
- Gmail account with an App Password

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

**To and CC:** `EMAIL_RECIPIENTS` is the main To list (comma-separated). To copy others without putting them in To, set optional `EMAIL_CC` to a comma-separated list of addresses.

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

### 5. Set up Gmail App Password

1. Go to [Google Account Security](https://myaccount.google.com/security)
2. Enable 2-Step Verification if not already enabled
3. Go to **App Passwords** (search for it in account settings)
4. Generate a new app password for "Mail"
5. Copy the 16-character password to `GMAIL_APP_PASSWORD` in your `.env`

### 6. Configure the SQL query

Set `ZOHO_SQL_QUERY` in your `.env` to the SQL query that exports your data:

```
ZOHO_SQL_QUERY=select * from "YourTableName"
```

## Daily Usage

1. Connect to the VPN
2. Run the script:

```bash
python main.py
```

The script will:
- Verify connectivity to the SFTP server
- Export data from Zoho Analytics
- Save the CSV to `./exports/mtn_rw_YYYY-MM-DD_HHMMSS.csv`
- Upload the file to the SFTP server
- Send an email notification with the file attached

## Testing locally

The script is end-to-end: it needs a working VPN to the SFTP host, valid Zoho credentials, and Gmail.

1. **Create a virtual environment (optional but recommended)**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Use a real `.env`** with all variables filled. Point `EMAIL_RECIPIENTS` (and `EMAIL_CC` if needed) to your own test addresses for the first run if you want to avoid sending to production lists.

3. **Connect the VPN** so `10.150.97.169:9000` is reachable. The first step the script runs is a TCP check to the SFTP host; if that fails, fix VPN before debugging anything else.

4. **Run**

   ```bash
   python main.py
   ```

5. **Verify:** Check `./exports/` for the CSV, confirm the file on the SFTP server, and check your inbox (and CC inboxes) for the email. If something fails, the script prints which step failed.

You cannot fully test the SFTP step without network access to that host; the Zoho and Gmail parts need real API and SMTP credentials (no separate mock mode in this project).

## Troubleshooting

### "Cannot reach SFTP server"
- Make sure you are connected to the VPN
- Check that the VPN is active and connected

### "Failed to get Zoho access token"
- Set `ZOHO_DATA_CENTER` to your Zoho region (`us`/`com`, `eu`, `in`, etc.); US orgs usually use `us` or `com`
- The refresh token must come from the **same** API client as `ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET` (if you recreated the client, generate a new refresh token)
- Do not paste a one-time **authorization code** where the **refresh token** belongs; you need the `refresh_token` field from the token exchange response

### "SFTP authentication failed"
- Check your SFTP username and password in `.env`

### "Gmail authentication failed"
- Make sure you're using an App Password, not your regular Gmail password
- Verify 2-Step Verification is enabled on your Google account

### Script says the email was sent but nothing arrived
- Confirm the **To** / **CC** lines printed at the end match the inboxes you are checking
- Check **Spam**, **Promotions** (Gmail), and quarantine in corporate email
- A successful SMTP handoff only means Gmail accepted the message; the receiving domain may still filter or delay it
