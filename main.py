#!/usr/bin/env python3
import json
import os
import socket
import sys
import time
from datetime import datetime, UTC
from pathlib import Path

import paramiko
import requests
from dotenv import load_dotenv

ZOHO_DATA_CENTERS: dict[str, tuple[str, str]] = {
    "us": ("https://accounts.zoho.com", "https://analyticsapi.zoho.com"),
    "com": ("https://accounts.zoho.com", "https://analyticsapi.zoho.com"),
    "eu": ("https://accounts.zoho.eu", "https://analyticsapi.zoho.eu"),
    "in": ("https://accounts.zoho.in", "https://analyticsapi.zoho.in"),
    "au": ("https://accounts.zoho.com.au", "https://analyticsapi.zoho.com.au"),
    "jp": ("https://accounts.zoho.jp", "https://analyticsapi.zoho.jp"),
    "uk": ("https://accounts.zoho.uk", "https://analyticsapi.zoho.uk"),
    "ca": ("https://accounts.zohocloud.ca", "https://analyticsapi.zohocloud.ca"),
    "sa": ("https://accounts.zoho.sa", "https://analyticsapi.zoho.sa"),
}

REQUIRED_ENV_VARS = [
    "ZOHO_CLIENT_ID",
    "ZOHO_CLIENT_SECRET",
    "ZOHO_REFRESH_TOKEN",
    "ZOHO_ORG_ID",
    "ZOHO_WORKSPACE_ID",
    "ZOHO_SQL_QUERY",
    "SFTP_HOST",
    "SFTP_PORT",
    "SFTP_USERNAME",
    "SFTP_PASSWORD",
    "GCHAT_WEBHOOK_URL",
]


def load_config() -> dict:
    load_dotenv()
    config = {}
    missing = []
    for var in REQUIRED_ENV_VARS:
        value = os.getenv(var)
        if not value:
            missing.append(var)
        config[var] = value
    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
        print("Please check your .env file and ensure all required values are set.")
        sys.exit(1)
    config["SFTP_REMOTE_DIR"] = (os.getenv("SFTP_REMOTE_DIR") or "").strip()
    for k in list(config):
        if isinstance(config[k], str):
            config[k] = config[k].strip()
    config["SFTP_PORT"] = int(config["SFTP_PORT"])
    dc = (os.getenv("ZOHO_DATA_CENTER") or "us").strip().lower()
    if dc not in ZOHO_DATA_CENTERS:
        print(
            f"ERROR: ZOHO_DATA_CENTER must be one of: {', '.join(sorted(ZOHO_DATA_CENTERS))}"
        )
        sys.exit(1)
    acc, api = ZOHO_DATA_CENTERS[dc]
    if os.getenv("ZOHO_ACCOUNTS_BASE_URL", "").strip():
        acc = os.getenv("ZOHO_ACCOUNTS_BASE_URL", "").strip()
    if os.getenv("ZOHO_ANALYTICS_BASE_URL", "").strip():
        api = os.getenv("ZOHO_ANALYTICS_BASE_URL", "").strip()
    config["ZOHO_ACCOUNTS_BASE"] = acc.rstrip("/")
    config["ZOHO_ANALYTICS_BASE"] = api.rstrip("/")
    return config


def check_connectivity(host: str, port: int, timeout: int = 10) -> bool:
    print(f"Checking connectivity to SFTP server at {host}:{port}...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        print(f"SUCCESS: SFTP server is reachable.")
        return True
    except socket.error as e:
        print(f"ERROR: Cannot reach SFTP server at {host}:{port}")
        print("Please check your VPN connection and try again.")
        print(f"Details: {e}")
        return False


def get_zoho_access_token(config: dict) -> str:
    print("Obtaining Zoho access token...")
    base = config["ZOHO_ACCOUNTS_BASE"]
    url = f"{base}/oauth/v2/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": config["ZOHO_CLIENT_ID"],
        "client_secret": config["ZOHO_CLIENT_SECRET"],
        "refresh_token": config["ZOHO_REFRESH_TOKEN"],
    }
    response = requests.post(url, data=data, timeout=60)
    if response.status_code != 200:
        print(f"ERROR: Failed to get Zoho access token. Status: {response.status_code}")
        print(f"Response: {response.text}")
        print(
            "Hints: (1) Set ZOHO_DATA_CENTER to your Zoho region "
            "(e.g. eu, in, com/us for the US .com org) — wrong region often causes this. "
            "(2) The refresh token must be generated with the *same* Zoho API client_id "
            "and client_secret in this .env. "
            "(3) Re-create the token if the old one was revoked or is from another client."
        )
        print(f"Current token URL: {url}")
        sys.exit(1)
    try:
        token_data = response.json()
    except json.JSONDecodeError:
        print(f"ERROR: Unexpected response (not JSON): {response.text[:500]}")
        sys.exit(1)
    if "access_token" not in token_data:
        err = token_data.get("error", "")
        print(f"ERROR: No access_token in response: {token_data}")
        if err == "invalid_code":
            print(
                "Zoho returned 'invalid_code' for a refresh request: your ZOHO_REFRESH_TOKEN "
                "is probably the *one-time grant code* from 'Generate code' in the API Console. "
                "That is not a refresh token. You must run a *separate* request with "
                "grant_type=authorization_code and the grant code, then put the *refresh_token* "
                "field from the JSON response into .env. See README (Zoho) step 3."
            )
        elif err:
            print(
                "If this is a refresh request: check ZOHO_DATA_CENTER, client_id, client_secret, "
                "and that ZOHO_REFRESH_TOKEN is the long refresh_token (not the grant code)."
            )
        sys.exit(1)
    print("SUCCESS: Obtained Zoho access token.")
    return token_data["access_token"]


def create_export_job(config: dict, access_token: str) -> str:
    print("Creating Zoho Analytics export job...")
    workspace_id = config["ZOHO_WORKSPACE_ID"]
    org_id = config["ZOHO_ORG_ID"]
    base = config["ZOHO_ANALYTICS_BASE"]
    url = f"{base}/restapi/v2/bulk/workspaces/{workspace_id}/data"
    export_config = {
        "sqlQuery": config["ZOHO_SQL_QUERY"],
        "responseFormat": "csv",
    }
    config_json = json.dumps(export_config)
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "ZANALYTICS-ORGID": org_id,
    }
    params = {"CONFIG": config_json}
    response = requests.get(url, headers=headers, params=params, timeout=120)
    if response.status_code != 200:
        print(f"ERROR: Failed to create export job. Status: {response.status_code}")
        print(f"Response: {response.text}")
        sys.exit(1)
    result = response.json()
    if "data" not in result or "jobId" not in result["data"]:
        print(f"ERROR: Unexpected response format: {result}")
        sys.exit(1)
    job_id = result["data"]["jobId"]
    print(f"SUCCESS: Export job created with ID: {job_id}")
    return job_id


def _as_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def poll_export_job(config: dict, access_token: str, job_id: str) -> None:
    print("Polling export job status...")
    workspace_id = config["ZOHO_WORKSPACE_ID"]
    org_id = config["ZOHO_ORG_ID"]
    base = config["ZOHO_ANALYTICS_BASE"]
    url = f"{base}/restapi/v2/bulk/workspaces/{workspace_id}/exportjobs/{job_id}"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "ZANALYTICS-ORGID": org_id,
    }
    while True:
        response = requests.get(url, headers=headers, timeout=60)
        if response.status_code != 200:
            print(f"ERROR: Failed to get job status. Status: {response.status_code}")
            print(f"Response: {response.text}")
            sys.exit(1)
        result = response.json()
        raw_code = result.get("data", {}).get("jobCode")
        job_code = _as_int(raw_code)
        job_status = result.get("data", {}).get("jobStatus", "Unknown")
        if job_code == 1001:
            print(f"  Status: Not initiated yet. Waiting...")
        elif job_code == 1002:
            print(f"  Status: In progress. Waiting...")
        elif job_code == 1003:
            print(f"ERROR: Export job failed. Status: {job_status}")
            print(f"Response: {result}")
            sys.exit(1)
        elif job_code == 1004:
            print("SUCCESS: Export job completed.")
            return
        elif job_code == 1005:
            print(f"ERROR: Job not found. Response: {result}")
            sys.exit(1)
        else:
            print(f"  Unknown job code: {raw_code!r} (parsed: {job_code}). Waiting...")
        time.sleep(5)


def download_export(config: dict, access_token: str, job_id: str) -> Path:
    print("Downloading exported CSV...")
    workspace_id = config["ZOHO_WORKSPACE_ID"]
    org_id = config["ZOHO_ORG_ID"]
    base = config["ZOHO_ANALYTICS_BASE"]
    url = f"{base}/restapi/v2/bulk/workspaces/{workspace_id}/exportjobs/{job_id}/data"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "ZANALYTICS-ORGID": org_id,
    }
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(f"ERROR: Failed to download export. Status: {response.status_code}")
        print(f"Response: {response.text}")
        sys.exit(1)
    exports_dir = Path("exports")
    exports_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
    filename = f"mtn_rw_{timestamp}.csv"
    filepath = exports_dir / filename
    filepath.write_bytes(response.content)
    file_size_mb = len(response.content) / (1024 * 1024)
    print(f"SUCCESS: Saved to {filepath} ({file_size_mb:.2f} MB)")
    return filepath


def sftp_remote_object_path(remote_dir: str, filename: str) -> str:
    d = (remote_dir or "").strip()
    if d in ("", "/", "."):
        return filename
    return f"{d.rstrip('/')}/{filename}"


def upload_to_sftp(config: dict, local_file: Path) -> tuple[str, str]:
    print(f"Uploading {local_file.name} to SFTP server...")
    host = config["SFTP_HOST"]
    port = config["SFTP_PORT"]
    username = config["SFTP_USERNAME"]
    password = config["SFTP_PASSWORD"]
    remote_dir = config.get("SFTP_REMOTE_DIR") or "/"
    primary = sftp_remote_object_path(remote_dir, local_file.name)
    attempts = [primary]
    if primary == local_file.name:
        attempts.append(f"upload/{local_file.name}")
    try:
        transport = paramiko.Transport((host, port))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)

        cwd = sftp.getcwd() or "."
        print(f"  SFTP session working directory: {cwd!r}")

        used = primary
        for path in attempts:
            try:
                sftp.put(str(local_file), path)
                used = path
                break
            except OSError as e:
                if e.errno != 13 or path == attempts[-1]:
                    raise
                print(f"  Permission denied writing to {path!r}, trying fallback...")

        # Verify the file actually landed on the server
        try:
            remote_stat = sftp.stat(used)
            local_size = local_file.stat().st_size
            remote_size = remote_stat.st_size
            print(f"  Verified remote file: {used!r} ({remote_size} bytes)")
            if remote_size != local_size:
                print(
                    f"  WARNING: Size mismatch! Local={local_size} bytes, "
                    f"Remote={remote_size} bytes. The file may be corrupt or truncated."
                )
        except OSError as stat_err:
            print(
                f"  WARNING: Could not stat remote file after upload: {stat_err}\n"
                f"  The put() call succeeded but the file could not be confirmed at {used!r}."
            )

        # List the target directory so it's visible in logs
        try:
            target_dir = str(Path(used).parent) if "/" in used else "."
            listing = sftp.listdir(target_dir)
            print(f"  Remote directory listing ({target_dir!r}): {listing}")
        except OSError:
            pass

        sftp.close()
        transport.close()
        if used != primary:
            print(
                "Note: Upload used a fallback path (upload/). For Docker atmoz/sftp, set "
                "SFTP_REMOTE_DIR=upload in .env so this is explicit."
            )
        print(f"SUCCESS: Uploaded to {used}")
        return used, cwd
    except paramiko.AuthenticationException:
        print("ERROR: SFTP authentication failed. Check your username/password.")
        sys.exit(1)
    except paramiko.SSHException as e:
        print(f"ERROR: SFTP connection error: {e}")
        sys.exit(1)
    except OSError as e:
        print(f"ERROR: SFTP upload failed: {e}")
        if e.errno == 13:
            print(
                "Hint: The server rejected writing to that path. For the atmoz/sftp Docker "
                "image, set SFTP_REMOTE_DIR=upload (or the subfolder you configured for the user)."
            )
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: SFTP upload failed: {e}")
        if getattr(e, "errno", None) == 13:
            print(
                "Hint: For Docker SFTP, set SFTP_REMOTE_DIR=upload (or the folder you created for the user)."
            )
        sys.exit(1)


def send_gchat_notification(
    config: dict,
    local_file: Path,
    remote_path: str,
    sftp_cwd: str,
) -> None:
    print("Sending Google Chat notification...")
    webhook_url = config["GCHAT_WEBHOOK_URL"]
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    message = {
        "text": (
            "*MTN RW Daily SFTP Upload Complete*\n\n"
            f"File: `{local_file.name}`\n"
            f"SFTP session directory: `{sftp_cwd}`\n"
            f"Remote path: `{remote_path}`\n"
            f"Timestamp: {timestamp}"
        )
    }
    try:
        response = requests.post(webhook_url, json=message, timeout=30)
        if response.status_code != 200:
            print(f"ERROR: Google Chat notification failed. Status: {response.status_code}")
            print(f"Response: {response.text}")
            sys.exit(1)
        print("SUCCESS: Posted notification to Google Chat.")
    except Exception as e:
        print(f"ERROR: Failed to send Google Chat notification: {e}")
        sys.exit(1)


def main():
    print("=" * 60)
    print("MTN RW Daily SFTP Upload Script")
    print("=" * 60)
    print()

    config = load_config()

    if not check_connectivity(config["SFTP_HOST"], config["SFTP_PORT"]):
        sys.exit(1)
    print()

    access_token = get_zoho_access_token(config)
    print()

    job_id = create_export_job(config, access_token)
    print()

    poll_export_job(config, access_token, job_id)
    print()

    local_file = download_export(config, access_token, job_id)
    print()

    remote_path, sftp_cwd = upload_to_sftp(config, local_file)
    print()

    send_gchat_notification(config, local_file, remote_path, sftp_cwd)
    print()

    print("=" * 60)
    print("COMPLETE!")
    print(f"  File: {local_file.name}")
    print(f"  SFTP session directory: {sftp_cwd}")
    print(f"  SFTP: {remote_path}")
    print(f"  Notified: Google Chat")
    print("=" * 60)


if __name__ == "__main__":
    main()
