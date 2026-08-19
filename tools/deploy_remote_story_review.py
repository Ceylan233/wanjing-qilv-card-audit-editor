#!/usr/bin/env python3
"""交互式部署故事核验同步服务；密码只从终端输入，不写入文件。"""

from __future__ import annotations

import getpass
from pathlib import Path

import paramiko


PROJECT = Path(__file__).resolve().parents[1]
SERVER_FILE = PROJECT / "tools" / "remote_card_audit_server.py"
STORY_FILE = PROJECT / ".codex-temp" / "storybooks_ocr_zh" / "story_review_entries_narrowed.json"
NGINX_STORY_FILE = PROJECT / "tools" / "remote_card_audit_story_nginx.location"
HOST = "47.95.121.98"


def main() -> int:
    password = getpass.getpass("SSH password: ")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username="root", password=password, timeout=20, banner_timeout=20, auth_timeout=20)
    stdin, stdout, stderr = client.exec_command(
        "cp -f /opt/wanjing-card-audit/remote_card_audit_server.py "
        "/opt/wanjing-card-audit/remote_card_audit_server.py.bak-20260819-story-review "
        "&& mkdir -p /opt/wanjing-card-audit/data"
    )
    if stdout.channel.recv_exit_status() != 0:
        raise RuntimeError(stderr.read().decode("utf-8", "replace")[:500])
    sftp = client.open_sftp()
    sftp.put(str(SERVER_FILE), "/opt/wanjing-card-audit/remote_card_audit_server.py")
    sftp.put(str(STORY_FILE), "/opt/wanjing-card-audit/data/story_review_entries_narrowed.json")
    stdin, stdout, stderr = client.exec_command(
        "grep -RIl 'wanjing-card-audit/manual_card_audit.json' /etc/nginx 2>/dev/null | head -1"
    )
    nginx_target = stdout.read().decode("utf-8", "replace").strip()
    if nginx_target:
        stdin, stdout, stderr = client.exec_command(
            "rm -f /etc/nginx/sites-enabled/syncinema.bak-20260819-story-review && "
            "cp -f " + nginx_target + " /opt/wanjing-card-audit/syncinema.bak-20260819-story-review"
        )
        if stdout.channel.recv_exit_status() != 0:
            raise RuntimeError(stderr.read().decode("utf-8", "replace")[:500])
        sftp.put(str(NGINX_STORY_FILE), "/tmp/wanjing-card-audit-story.location")
    sftp.close()
    command = (
        "systemctl restart remote_card_audit_sync.service && sleep 1 && "
        "systemctl is-active remote_card_audit_sync.service && "
        "stat -c '%n %s' /opt/wanjing-card-audit/data/story_review_entries_narrowed.json"
    )
    if nginx_target:
        command += (
            " && if ! grep -q 'story_review_entries_narrowed.json' " + nginx_target + "; then "
            "sed -i '/# Keep the Syncinema application/r /tmp/wanjing-card-audit-story.location' " + nginx_target + "; fi"
            " && nginx -t && systemctl reload nginx && echo nginx_reloaded"
        )
    stdin, stdout, stderr = client.exec_command(command)
    status = stdout.channel.recv_exit_status()
    output = stdout.read().decode("utf-8", "replace").strip()
    error = stderr.read().decode("utf-8", "replace").strip()
    if status:
        raise RuntimeError(error[:500] or output[:500])
    print(output)
    if not nginx_target:
        print("nginx_location=not_found")
        stdin, stdout, stderr = client.exec_command(
            "systemctl list-units --type=service --state=running --no-legend 2>/dev/null | "
            "grep -Ei 'nginx|caddy|traefik|apache' || true; "
            "nginx -T 2>/dev/null | grep -n -Ei 'syncinema|wanjing-card-audit|proxy_pass|include' | tail -80 || true"
        )
        print(stdout.read().decode("utf-8", "replace").strip())
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
