#!/usr/bin/env python3
import json
import os
import socket
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

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
    "ZOHO_SQL_QUERY_2",
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
    config["ZOHO_EXPORT_1_FILENAME_PREFIX"] = (
        os.getenv("ZOHO_EXPORT_1_FILENAME_PREFIX") or "mtn_rw_contracts"
    ).strip()
    config["ZOHO_EXPORT_2_FILENAME_PREFIX"] = (
        os.getenv("ZOHO_EXPORT_2_FILENAME_PREFIX") or "mtn_rw_payins"
    ).strip()
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


def create_export_job(config: dict, access_token: str, sql_query: str, label: str) -> str:
    print(f"Creating Zoho Analytics export job ({label})...")
    workspace_id = config["ZOHO_WORKSPACE_ID"]
    org_id = config["ZOHO_ORG_ID"]
    base = config["ZOHO_ANALYTICS_BASE"]
    url = f"{base}/restapi/v2/bulk/workspaces/{workspace_id}/data"
    export_config = {
        "sqlQuery": sql_query,
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


def poll_export_job(config: dict, access_token: str, job_id: str, label: str) -> None:
    print(f"Polling export job status ({label})...")
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


@dataclass
class ExportResult:
    local_file: Path
    job_id: str
    label: str


def download_export(
    config: dict,
    access_token: str,
    job_id: str,
    filename_prefix: str,
    timestamp: str,
    label: str,
) -> Path:
    print(f"Downloading exported CSV ({label})...")
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
        print(f"ERROR: Failed to download export ({label}). Status: {response.status_code}")
        print(f"Response: {response.text}")
        sys.exit(1)
    exports_dir = Path("exports")
    exports_dir.mkdir(exist_ok=True)
    filename = f"{filename_prefix}_{timestamp}.csv"
    filepath = exports_dir / filename
    filepath.write_bytes(response.content)
    file_size_mb = len(response.content) / (1024 * 1024)
    print(f"SUCCESS: Saved to {filepath} ({file_size_mb:.2f} MB)")
    return filepath


def run_export(
    config: dict,
    access_token: str,
    sql_query: str,
    filename_prefix: str,
    timestamp: str,
    label: str,
) -> ExportResult:
    job_id = create_export_job(config, access_token, sql_query, label)
    print()
    poll_export_job(config, access_token, job_id, label)
    print()
    local_file = download_export(
        config, access_token, job_id, filename_prefix, timestamp, label
    )
    return ExportResult(local_file=local_file, job_id=job_id, label=label)


def sftp_remote_object_path(remote_dir: str, filename: str) -> str:
    d = (remote_dir or "").strip()
    if d in ("", "/", "."):
        return filename
    return f"{d.rstrip('/')}/{filename}"


def format_sftp_cwd(cwd: str | None) -> str:
    if cwd is None:
        return "(not reported by server)"
    return cwd


def sftp_resolved_path(cwd: str | None, remote_path: str) -> str:
    if remote_path.startswith("/"):
        return remote_path
    if cwd is None:
        return f"{remote_path} (relative; session directory not reported)"
    base = PurePosixPath(cwd if cwd not in ("", ".") else ".")
    return str(base / remote_path)


def format_size_mb(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.2f} MB"


def format_duration(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {secs}s"


@dataclass
class SftpUploadResult:
    local_file: Path
    remote_path: str
    sftp_cwd: str | None
    used_fallback: bool
    local_size: int
    remote_size: int | None
    size_matched: bool | None


def _upload_file_on_sftp(
    sftp: paramiko.SFTPClient,
    local_file: Path,
    remote_dir: str,
    list_dir_after: bool,
) -> SftpUploadResult:
    primary = sftp_remote_object_path(remote_dir, local_file.name)
    attempts = [primary]
    if primary == local_file.name:
        attempts.append(f"upload/{local_file.name}")

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

    local_size = local_file.stat().st_size
    remote_size: int | None = None
    size_matched: bool | None = None
    try:
        remote_stat = sftp.stat(used)
        remote_size = remote_stat.st_size
        size_matched = remote_size == local_size
        print(f"  Verified remote file: {used!r} ({remote_size} bytes)")
        if not size_matched:
            print(
                f"  WARNING: Size mismatch! Local={local_size} bytes, "
                f"Remote={remote_size} bytes. The file may be corrupt or truncated."
            )
    except OSError as stat_err:
        print(
            f"  WARNING: Could not stat remote file after upload: {stat_err}\n"
            f"  The put() call succeeded but the file could not be confirmed at {used!r}."
        )

    if list_dir_after:
        try:
            target_dir = str(Path(used).parent) if "/" in used else "."
            listing = sftp.listdir(target_dir)
            print(f"  Remote directory listing ({target_dir!r}): {listing}")
        except OSError:
            pass

    if used != primary:
        print(
            "Note: Upload used a fallback path (upload/). For Docker atmoz/sftp, set "
            "SFTP_REMOTE_DIR=upload in .env so this is explicit."
        )
    print(f"SUCCESS: Uploaded to {used}")
    return SftpUploadResult(
        local_file=local_file,
        remote_path=used,
        sftp_cwd=sftp.getcwd(),
        used_fallback=used != primary,
        local_size=local_size,
        remote_size=remote_size,
        size_matched=size_matched,
    )


def upload_files_to_sftp(config: dict, local_files: list[Path]) -> list[SftpUploadResult]:
    if not local_files:
        return []

    print(f"Uploading {len(local_files)} file(s) to SFTP server...")
    host = config["SFTP_HOST"]
    port = config["SFTP_PORT"]
    username = config["SFTP_USERNAME"]
    password = config["SFTP_PASSWORD"]
    remote_dir = config.get("SFTP_REMOTE_DIR") or "/"
    try:
        transport = paramiko.Transport((host, port))
        transport.connect(username=username, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)

        cwd = sftp.getcwd()
        print(f"  SFTP session working directory: {format_sftp_cwd(cwd)!r}")

        uploads: list[SftpUploadResult] = []
        for index, local_file in enumerate(local_files):
            print(f"Uploading {local_file.name}...")
            uploads.append(
                _upload_file_on_sftp(
                    sftp,
                    local_file,
                    remote_dir,
                    list_dir_after=index == len(local_files) - 1,
                )
            )

        sftp.close()
        transport.close()
        return uploads
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


def _format_size_line(upload: SftpUploadResult) -> str:
    local = format_size_mb(upload.local_size)
    if upload.remote_size is None:
        return f"Size: {local} (local) / unknown (remote)"
    remote = format_size_mb(upload.remote_size)
    if upload.size_matched is True:
        return f"Size: {local} (local) / {remote} (remote) ✓"
    if upload.size_matched is False:
        return f"Size: {local} (local) / {remote} (remote) ⚠️ mismatch"
    return f"Size: {local} (local) / {remote} (remote)"


def _format_upload_block(upload: SftpUploadResult) -> str:
    resolved = sftp_resolved_path(upload.sftp_cwd, upload.remote_path)
    fallback = "yes" if upload.used_fallback else "no"
    return (
        f"File: `{upload.local_file.name}`\n"
        f"Remote: `{upload.remote_path}`\n"
        f"Resolved: `{resolved}`\n"
        f"{_format_size_line(upload)}\n"
        f"Fallback path used: {fallback}"
    )


def send_gchat_notification(
    config: dict,
    exports: list[ExportResult],
    uploads: list[SftpUploadResult],
    duration_seconds: float,
) -> None:
    print("Sending Google Chat notification...")
    webhook_url = config["GCHAT_WEBHOOK_URL"]
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    remote_dir = (config.get("SFTP_REMOTE_DIR") or "").strip()
    configured_dir = remote_dir if remote_dir else "(not set)"
    cwd_display = format_sftp_cwd(uploads[0].sftp_cwd if uploads else None)
    file_blocks = "\n\n".join(_format_upload_block(upload) for upload in uploads)
    job_lines = "\n".join(
        f"Zoho job ({export.label}): `{export.job_id}`" for export in exports
    )
    message = {
        "text": (
            "*MTN RW Daily SFTP Upload Complete*\n\n"
            f"Server: `{config['SFTP_HOST']}:{config['SFTP_PORT']}`  "
            f"User: `{config['SFTP_USERNAME']}`\n"
            f"Configured dir: `{configured_dir}`\n"
            f"Session directory: `{cwd_display}`\n\n"
            f"{file_blocks}\n\n"
            f"Timestamp: {timestamp}\n"
            f"{job_lines}\n"
            f"Duration: {format_duration(duration_seconds)}"
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
    run_started = time.monotonic()
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

    # Filename date range: today and the previous 2 days (YYYY_MM_DD-YYYY_MM_DD)
    today = datetime.now(UTC).date()
    start_date = today - timedelta(days=2)
    date_range = (
        f"{start_date.strftime('%Y_%m_%d')}-{today.strftime('%Y_%m_%d')}"
    )
    export_specs = [
        ("contracts", config["ZOHO_SQL_QUERY"], config["ZOHO_EXPORT_1_FILENAME_PREFIX"]),
        (
            "payins",
            config["ZOHO_SQL_QUERY_2"],
            config["ZOHO_EXPORT_2_FILENAME_PREFIX"],
        ),
    ]
    exports: list[ExportResult] = []
    for label, sql_query, filename_prefix in export_specs:
        exports.append(
            run_export(
                config,
                access_token,
                sql_query,
                filename_prefix,
                date_range,
                label,
            )
        )
        print()

    uploads = upload_files_to_sftp(config, [export.local_file for export in exports])
    print()

    duration_seconds = time.monotonic() - run_started
    send_gchat_notification(config, exports, uploads, duration_seconds)
    print()

    print("=" * 60)
    print("COMPLETE!")
    for export, upload in zip(exports, uploads):
        print(f"  File: {export.local_file.name}")
        print(f"  SFTP: {upload.remote_path}")
    print(f"  SFTP session directory: {format_sftp_cwd(uploads[0].sftp_cwd)}")
    print(f"  Duration: {format_duration(duration_seconds)}")
    print(f"  Notified: Google Chat")
    print("=" * 60)


if __name__ == "__main__":
    main()
