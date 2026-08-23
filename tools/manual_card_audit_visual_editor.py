#!/usr/bin/env python3
"""万境奇旅卡牌人工校对器（表单版）。"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import stat
import ssl
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
from urllib.error import HTTPError
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any
from urllib.parse import unquote

try:
    import certifi
except ImportError:  # 源码环境未安装时仍尝试使用系统证书
    certifi = None

from PIL import Image, ImageDraw, ImageTk

try:
    from editor_build_version import EDITOR_VERSION as EMBEDDED_EDITOR_VERSION
except ImportError:
    EMBEDDED_EDITOR_VERSION = ""

from card_summary import (
    SUMMARY_SCHEMA_VERSION,
    build_card_summary,
    card_kind,
    ensure_card_summary,
    load_ability_index,
)


def find_project() -> Path:
    start = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    roots = [start, Path.cwd()]
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        roots.append(Path(appimage).resolve().parent)
    for root in roots:
        for candidate in (root, *root.parents):
            if (candidate / "data/rules/zh_cn/manual_card_audit.json").exists():
                return candidate
    return start


PROJECT = find_project()
DEFAULT_JSON = PROJECT / "data/rules/zh_cn/manual_card_audit.json"
DIR_KEYS = [("上_北", "北/上"), ("右_东", "东/右"), ("下_南", "南/下"), ("左_西", "西/左")]
SKILLS = ["未识别", "移动（蓝色）", "查看（紫色）", "接触（橙色）", "帮助（绿色）", "获取（黄色）", "压制（红色）"]
SLOT_TYPES = ["技能类型骰槽", "能力要求骰槽", "指定结果骰槽", "互动元素通配骰槽", "地形限定骰槽", "特定行动额外投骰", "未分类骰槽"]
RESULTS = ["任意结果/未指定", "生命", "士气", "时间", "空白", "挫折/返回", "星星"]
COLORS = ["无/未识别", "蓝色", "紫色", "橙色", "绿色", "黄色", "红色"]
ROAD_STATES = ["直接相邻地点", "星号/查离开故事书", "空白/不可通行", "空白/不可通行或未识别"]
SMALL_TYPES = ["未分类/不适用", "物品", "动物", "智慧生物/NPC", "植物", "构筑物", "载具"]
ACTION_FAMILIES = ["导航", "进入", "交谈", "提供", "探访", "对决"]
STORY_BOOKS = ["移动故事书", "查看故事书", "接触故事书", "帮助故事书", "获取故事书", "压制故事书", "card"]
EXECUTION_STATES = ["ready", "needs_review", "blocked", "disabled"]
COST_CHOICES = [""] + [str(value) for value in range(0, 21)] + ["X"]
NUMBER_CHOICES = [""] + [str(value) for value in range(0, 101)]

SKILL_CODES = {
    "未识别": "", "移动（蓝色）": "move", "查看（紫色）": "look", "接触（橙色）": "engage",
    "帮助（绿色）": "help", "获取（黄色）": "take", "压制（红色）": "overpower",
}
SLOT_TYPE_CODES = {
    "技能类型骰槽": "skill_type", "能力要求骰槽": "ability", "指定结果骰槽": "specific_result",
    "互动元素通配骰槽": "interaction", "地形限定骰槽": "terrain",
    "特定行动额外投骰": "action_specific_dice_modifier", "未分类骰槽": "manual",
}
RESULT_CODES = {
    "任意结果/未指定": "", "生命": "health", "士气": "morale", "时间": "time",
    "空白": "blank", "挫折/返回": "setback", "星星": "star",
}
COLOR_CODES = {
    "无/未识别": "", "蓝色": "blue", "紫色": "purple", "橙色": "orange",
    "绿色": "green", "黄色": "yellow", "红色": "red",
}
SMALL_TYPE_CODES = {
    "未分类/不适用": "", "物品": "item", "动物": "animal", "智慧生物/NPC": "sentient",
    "植物": "flora", "构筑物": "structure", "载具": "vehicle",
}
STORY_BOOK_CODES = {
    "移动故事书": "move", "查看故事书": "look", "接触故事书": "engage", "帮助故事书": "help",
    "获取故事书": "take", "压制故事书": "overpower", "card": "card",
}
CHALLENGE_SLOT_WIDTH = 334.0
CHALLENGE_SLOT_HEIGHT = 327.0
DEFAULT_IMAGE_ROTATIONS = {
    **{str(number).zfill(4): 180 for number in range(1092, 1105)},
    **{str(number).zfill(4): 90 for number in range(1265, 1294)},
    "1138": 180,
    "1139": 180,
    "1146": 180,
    "1150": 90,
}
EDITOR_VERSION = re.sub(
    r"^(?:editor-)?v",
    "",
    os.environ.get("CARD_AUDIT_EDITOR_VERSION") or EMBEDDED_EDITOR_VERSION or "0.3.36",
    flags=re.IGNORECASE,
)
UPDATE_REPOSITORY = "Ceylan233/wanjing-qilv-card-audit-editor"
WINDOWS_UPDATE_ASSET = "wanjing-card-audit-editor-windows.exe"
LINUX_UPDATE_ASSET = "wanjing-card-audit-editor-linux.AppImage"
UPDATE_CHECKSUM_ASSET = "SHA256SUMS.txt"
REMOTE_CONFIG_ENV = "CARD_AUDIT_REMOTE_CONFIG_URL"
REMOTE_SECURITY_CODE_ENV = "CARD_AUDIT_SECURITY_CODE"
REMOTE_CACHE_DIR = Path.home() / ".wanjing-card-audit-editor"
REMOTE_URL_FILE = REMOTE_CACHE_DIR / "remote_config_url.txt"
REMOTE_CODE_FILE = REMOTE_CACHE_DIR / "security_code.txt"


def version_numbers(value: str) -> tuple[int, ...]:
    numbers = tuple(int(part) for part in re.findall(r"\d+", value))
    return numbers or (0,)


def windows_update_command(script_path: Path) -> list[str]:
    """使用 Windows Unicode 命令行直接启动更新脚本，兼容中文安装路径。"""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    executable = str(powershell) if powershell.is_file() else "powershell.exe"
    return [
        executable,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-File",
        str(script_path),
    ]


def independent_frozen_process_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Create a clean environment for a new frozen application instance."""
    environment = dict(os.environ if source is None else source)
    for key in list(environment):
        if key.upper().startswith("_PYI_"):
            environment.pop(key, None)
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def launch_windows_update_script(script_path: Path) -> None:
    command = windows_update_command(script_path)
    # DETACHED_PROCESS 会让 Windows PowerShell 5.1 返回成功却跳过 -File 脚本；
    # CREATE_NO_WINDOW 已能隐藏窗口，独立进程组则允许编辑器安全退出。
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    options = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "env": independent_frozen_process_environment(),
    }
    try:
        subprocess.Popen(command, creationflags=flags, **options)
    except OSError:
        subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW, **options)


def download_url(url: str, target: Path | None = None) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": f"wanjing-card-audit-editor/{EDITOR_VERSION}"})
    with urllib.request.urlopen(request, timeout=60, context=build_ssl_context()) as response:
        if target is None:
            return response.read()
        with target.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    return None


def build_ssl_context() -> ssl.SSLContext:
    """为 PyInstaller/AppImage 显式寻找 CA 证书，避免 Steam Deck 缺省路径为空。"""
    candidates: list[str] = []
    configured = os.environ.get("SSL_CERT_FILE")
    if configured:
        candidates.append(configured)
    if certifi is not None:
        try:
            candidates.append(certifi.where())
        except Exception:
            pass
    candidates.extend([
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/ssl/cert.pem",
        "/etc/pki/tls/certs/ca-bundle.crt",
    ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


def fetch_latest_release() -> dict[str, Any]:
    """读取最新版本；GitHub API 受限时改用 releases/latest 网页重定向。"""
    api_url = f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest"
    try:
        payload = download_url(api_url)
        return json.loads((payload or b"{}").decode("utf-8"))
    except Exception as api_exc:
        try:
            page_url = f"https://github.com/{UPDATE_REPOSITORY}/releases/latest"
            request = urllib.request.Request(page_url, headers={"User-Agent": f"wanjing-card-audit-editor/{EDITOR_VERSION}"})
            with urllib.request.urlopen(request, timeout=60, context=build_ssl_context()) as response:
                final_url = response.geturl()
            match = re.search(r"/releases/tag/([^/?#]+)", final_url)
            if not match:
                raise RuntimeError(f"未能从 GitHub 重定向地址识别版本：{final_url}")
            tag = unquote(match.group(1))
            download_base = f"https://github.com/{UPDATE_REPOSITORY}/releases/download/{tag}"
            return {
                "tag_name": tag,
                "body": "通过 GitHub 网页备用通道检查到的版本。",
                "assets": [
                    {"name": WINDOWS_UPDATE_ASSET, "browser_download_url": f"{download_base}/{WINDOWS_UPDATE_ASSET}"},
                    {"name": LINUX_UPDATE_ASSET, "browser_download_url": f"{download_base}/{LINUX_UPDATE_ASSET}"},
                    {"name": UPDATE_CHECKSUM_ASSET, "browser_download_url": f"{download_base}/{UPDATE_CHECKSUM_ASSET}"},
                ],
            }
        except Exception as fallback_exc:
            raise RuntimeError(f"GitHub API 检查失败：{api_exc}；网页备用检查也失败：{fallback_exc}") from fallback_exc


def fetch_json_url(url: str, headers: dict[str, str] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    request_headers = {"User-Agent": f"wanjing-card-audit-editor/{EDITOR_VERSION}"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=60, context=build_ssl_context()) as response:
        payload = json.loads(response.read().decode("utf-8-sig"))
        return payload, {str(key): str(value) for key, value in response.headers.items()}


def remote_auth_headers(config: dict[str, Any], code_override: str = "") -> dict[str, str]:
    # 安全码只存在当前进程内存，不写入远程配置或日志。
    code = code_override.strip() or str(config.get("session_code") or "").strip() or os.environ.get(REMOTE_SECURITY_CODE_ENV, "").strip()
    if not code:
        return {}
    header = str(config.get("auth_header") or config.get("安全码请求头") or "X-Card-Audit-Code").strip()
    headers = {header: code}
    # 兼容服务器切换期间的旧 Bearer 校验；新服务器只读取安全码请求头。
    if header.lower() != "authorization":
        headers["Authorization"] = f"Bearer {code}"
    return headers


def normalize_remote_config(config: dict[str, Any], config_url: str) -> dict[str, Any]:
    nested = config.get("远程校对") if isinstance(config.get("远程校对"), dict) else config
    document_url = str(
        nested.get("document_url")
        or nested.get("data_url")
        or nested.get("读取URL")
        or nested.get("数据URL")
        or (config_url if "卡牌" in config else "")
    ).strip()
    if not document_url:
        raise RuntimeError("远程配置缺少 document_url/data_url（数据读取 URL）")
    cache_name = str(nested.get("cache_file") or nested.get("本地缓存") or "manual_card_audit.remote.json").strip()
    cache_path = Path(cache_name).expanduser()
    if not cache_path.is_absolute():
        cache_path = REMOTE_CACHE_DIR / cache_path
    binding = dict(nested)
    binding.update({
        "config_url": config_url,
        "document_url": document_url,
        "upload_url": str(nested.get("upload_url") or nested.get("write_url") or nested.get("上传URL") or "").strip(),
        "method": str(nested.get("method") or nested.get("写入方法") or "PUT").upper(),
        "cache_path": cache_path,
        "auto_sync": bool(nested.get("auto_sync", nested.get("自动同步", True))),
    })
    return binding


def load_remote_document(config_url: str, code_override: str = "") -> tuple[Path, dict[str, Any], dict[str, Any]]:
    bootstrap_headers = remote_auth_headers({"session_code": code_override}, code_override)
    config, _ = fetch_json_url(config_url, bootstrap_headers)
    binding = normalize_remote_config(config, config_url)
    if code_override.strip():
        binding["session_code"] = code_override.strip()
    document, headers = fetch_json_url(binding["document_url"], remote_auth_headers(binding, code_override))
    if not isinstance(document, dict) or not isinstance(document.get("卡牌"), list):
        raise RuntimeError("远程数据不是有效的卡牌校对 JSON（缺少“卡牌”数组）")
    cache_path: Path = binding["cache_path"]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding["etag"] = headers.get("ETag") or headers.get("Etag") or ""
    return cache_path, binding, document


def save_remote_config_url(config_url: str) -> None:
    REMOTE_URL_FILE.parent.mkdir(parents=True, exist_ok=True)
    REMOTE_URL_FILE.write_text(config_url.strip() + "\n", encoding="utf-8")


def read_saved_remote_config_url() -> str:
    try:
        return REMOTE_URL_FILE.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def save_security_code(code: str) -> None:
    if not code.strip():
        return
    REMOTE_CODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    REMOTE_CODE_FILE.write_text(code.strip() + "\n", encoding="utf-8")
    try:
        REMOTE_CODE_FILE.chmod(0o600)
    except OSError:
        pass


def read_saved_security_code() -> str:
    try:
        return REMOTE_CODE_FILE.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def parse_int(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_cost(value: str) -> int | str | None:
    value = value.strip()
    if not value:
        return None
    if value.upper() == "X":
        return "X"
    return parse_int(value)


def text_get(widget: tk.Text) -> str:
    return widget.get("1.0", "end-1c")


def text_set(widget: tk.Text, value: Any) -> None:
    widget.delete("1.0", tk.END)
    widget.insert("1.0", "" if value is None else str(value))
    try:
        widget.edit_reset()
    except tk.TclError:
        pass


def add_entry(parent: tk.Widget, row: int, label: str, variable: tk.StringVar, width: int = 36, readonly: bool = False) -> ttk.Entry:
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=4)
    entry = ttk.Entry(parent, textvariable=variable, width=width, state="readonly" if readonly else "normal")
    entry.grid(row=row, column=1, sticky="ew", padx=6, pady=4)
    return entry


def add_combo(parent: tk.Widget, row: int, label: str, variable: tk.StringVar, values: list[str], width: int = 36) -> ttk.Combobox:
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=4)
    combo = ttk.Combobox(parent, textvariable=variable, values=values, width=width, state="readonly")
    combo.grid(row=row, column=1, sticky="ew", padx=6, pady=4)
    return combo


def ask_remote_settings(parent: tk.Misc, current_url: str = "", use_saved_code: bool = True) -> tuple[str, str] | None:
    """在同一个窗口配置远程 URL 和安全码；安全码只保存在本机缓存。"""
    dialog = tk.Toplevel(parent)
    dialog.title("配置远程校对")
    dialog.transient(parent)
    dialog.resizable(False, False)
    dialog.grab_set()
    url_var = tk.StringVar(value=current_url)
    token_var = tk.StringVar(value=read_saved_security_code() if use_saved_code else "")
    body = ttk.Frame(dialog, padding=14)
    body.grid(sticky="nsew")
    body.columnconfigure(1, weight=1)
    ttk.Label(body, text="配置 JSON URL").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
    url_entry = ttk.Entry(body, textvariable=url_var, width=68)
    url_entry.grid(row=0, column=1, sticky="ew", pady=6)
    ttk.Label(body, text="同步安全码").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
    token_entry = ttk.Entry(body, textvariable=token_var, show="*", width=68)
    token_entry.grid(row=1, column=1, sticky="ew", pady=6)
    ttk.Label(body, text="安全码只保存在本机缓存，不会写入远程 JSON。", foreground="#666666").grid(
        row=2, column=0, columnspan=2, sticky="w", pady=(2, 8)
    )
    result: list[tuple[str, str] | None] = [None]

    def accept() -> None:
        url = url_var.get().strip()
        token = token_var.get().strip()
        if not url or not token:
            messagebox.showwarning("信息不完整", "请同时填写配置 JSON URL 和同步安全码。", parent=dialog)
            return
        result[0] = (url, token)
        dialog.destroy()

    buttons = ttk.Frame(body)
    buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(4, 0))
    ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right", padx=(6, 0))
    ttk.Button(buttons, text="连接并加载", command=accept).pack(side="right")
    dialog.bind("<Return>", lambda _event: accept())
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    url_entry.focus_set()
    dialog.wait_window()
    return result[0]


class VisualAuditEditor(tk.Tk):
    def __init__(
        self,
        path: Path,
        remote_sync: dict[str, Any] | None = None,
        test_mode: bool = False,
    ):
        super().__init__()
        self.title(f"万境奇旅｜卡牌可视化人工校对器 v{EDITOR_VERSION}")
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        self.geometry(f"{min(1880, screen_width)}x{min(1080, screen_height)}+0+0")
        self.minsize(min(1180, max(900, screen_width - 80)), min(760, max(600, screen_height - 80)))
        self.path = path
        self.remote_sync: dict[str, Any] | None = remote_sync
        self.remote_sync_running = False
        self.closing = False
        self.document: dict[str, Any] = {}
        self.cards: list[dict[str, Any]] = []
        self.by_number: dict[str, dict[str, Any]] = {}
        try:
            self.abilities_by_card = load_ability_index(PROJECT / "data/rules/zh_cn/card_abilities.json")
        except Exception:
            self.abilities_by_card = {}
        self.summary_migration_count = 0
        self.current_number: str | None = None
        self.current_action_ref: tuple[str, int] | None = None
        self.current_slot_index: int | None = None
        self.current_boost_index: int | None = None
        self.image_tk: ImageTk.PhotoImage | None = None
        self.image_source: Image.Image | None = None
        self.display_scale = 1.0
        self.display_offset = (0.0, 0.0)
        self.image_zoom = 1.0
        self.image_rotation = 0
        self.image_focus_mode = False
        self.image_focus_label = tk.StringVar(value="大图预览")
        self.update_check_running = False
        self.story_review_window = None
        self.dirty = False
        self.backup_done = False
        self.loading = False
        self.document_load_generation = 0
        self.document_ready = False
        self.test_mode = test_mode
        self.user_modified_current = False
        self.pending_revision_numbers: set[str] = set()
        self.status_var = tk.StringVar(value="正在加载…")
        self.search_var = tk.StringVar()
        self.type_filter = tk.StringVar(value="全部")
        self.review_filter = tk.StringVar(value="全部")
        self.overlay_var = tk.BooleanVar(value=True)
        self.slot_edit_var = tk.BooleanVar(value=False)
        # 简洁模式只改变可见内容，不删除或改写任何底层字段。
        self.simple_mode_var = tk.BooleanVar(value=True)
        self.tab_pages: dict[str, tk.Widget] = {}
        self.slot_drag: dict[str, Any] | None = None
        self.undo_stack: list[tuple[str, dict[str, Any]]] = []
        self.redo_stack: list[tuple[str, dict[str, Any]]] = []
        self.slot_clipboard: dict[str, Any] | None = None
        self._build()
        if test_mode:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(document, dict) or not isinstance(document.get("卡牌"), list):
                raise ValueError("文件缺少“卡牌”数组")
            self.apply_document_data(path, document, remote_sync, synchronous=True)
        else:
            self.after(30, lambda: self.load_document_async(path, remote_sync))
            if remote_sync and not remote_sync.get("document_url"):
                self.after(80, self.load_remote)
            self.after_idle(self.maximize_window)
            self.after(1800, lambda: self.check_for_updates(silent=True))

    def _build(self) -> None:
        self.rowconfigure(1, weight=1)
        # 三栏均保持可用宽度；编辑表单内部有滚动区，不能让它的默认
        # Text(80 列)请求宽度把中央卡图挤成缩略图。
        self.columnconfigure(0, weight=0, minsize=220)
        self.columnconfigure(1, weight=5, minsize=360)
        self.columnconfigure(2, weight=4, minsize=560)
        toolbar = ttk.Frame(self, padding=6)
        toolbar.grid(row=0, column=0, columnspan=3, sticky="ew")
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, textvariable=self.status_var).grid(row=0, column=0, sticky="e")
        self._build_menu()

        self._build_card_list()
        self._build_image_panel()
        self._build_editor_tabs()
        self.enable_text_undo(self)
        self.bind("<Control-s>", lambda _e: self.save_all())
        self.bind("<Control-z>", self.handle_undo)
        self.bind("<Control-y>", self.handle_redo)
        self.bind("<Control-Shift-Z>", self.handle_redo)
        self.bind("<Control-c>", self.handle_copy_global)
        self.bind("<Control-v>", self.handle_paste_global)
        self.bind("<Control-x>", self.handle_cut_global)
        for class_name in ("Text", "Entry", "TEntry", "TCombobox"):
            self.bind_class(class_name, "<Control-z>", self.handle_undo)
            self.bind_class(class_name, "<Control-y>", self.handle_redo)
            self.bind_class(class_name, "<Control-Shift-Z>", self.handle_redo)
            self.bind_class(class_name, "<Control-c>", lambda event: self.handle_clipboard(event, "<<Copy>>"))
            self.bind_class(class_name, "<Control-v>", lambda event: self.handle_clipboard(event, "<<Paste>>"))
            self.bind_class(class_name, "<Control-x>", lambda event: self.handle_clipboard(event, "<<Cut>>"))
        self.bind_all("<FocusIn>", self.remember_entry_value, add="+")
        self.bind_all("<KeyRelease>", self.note_user_key_edit, add="+")
        self.bind_class("TCombobox", "<<ComboboxSelected>>", self.note_user_edit, add="+")
        self.bind_class("TCheckbutton", "<ButtonRelease-1>", self.note_user_edit, add="+")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_menu(self) -> None:
        """标准顶部分类菜单，替代占用大量空间的按钮框。"""
        menu_bar = tk.Menu(self, tearoff=False)

        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(label="打开校对文件…", accelerator="Ctrl+O", command=self.open_document)
        file_menu.add_command(label="保存到磁盘", accelerator="Ctrl+S", command=self.save_all)
        file_menu.add_separator()
        file_menu.add_command(label="导出待AI校对任务包…", command=self.export_ai_prompt_cards)
        file_menu.add_command(label="导出全部牌面总结…", command=self.export_all_card_summaries)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.on_close)
        menu_bar.add_cascade(label="文件", menu=file_menu)

        sync_menu = tk.Menu(menu_bar, tearoff=False)
        sync_menu.add_command(label="配置远程…", command=self.configure_remote)
        sync_menu.add_command(label="加载远程校对", command=self.load_remote)
        sync_menu.add_command(label="上传/同步远程", command=self.sync_remote)
        menu_bar.add_cascade(label="远程同步", menu=sync_menu)

        story_menu = tk.Menu(menu_bar, tearoff=False)
        story_menu.add_command(label="打开故事文本人工核验…", command=self.open_story_review_editor)
        menu_bar.add_cascade(label="故事核验", menu=story_menu)

        edit_menu = tk.Menu(menu_bar, tearoff=False)
        edit_menu.add_command(label="撤回", accelerator="Ctrl+Z", command=self.undo_card_change)
        edit_menu.add_command(label="重做", accelerator="Ctrl+Y", command=self.redo_card_change)
        edit_menu.add_separator()
        edit_menu.add_command(label="复制槽位", command=self.copy_selected_slot)
        edit_menu.add_command(label="粘贴槽位", command=self.paste_selected_slot)
        menu_bar.add_cascade(label="编辑", menu=edit_menu)

        nav_menu = tk.Menu(menu_bar, tearoff=False)
        nav_menu.add_command(label="上一张", accelerator="←", command=lambda: self.select_relative(-1))
        nav_menu.add_command(label="下一张", accelerator="→", command=lambda: self.select_relative(1))
        nav_menu.add_separator()
        nav_menu.add_command(label="检查更新", command=self.check_for_updates)
        menu_bar.add_cascade(label="导航", menu=nav_menu)

        view_menu = tk.Menu(menu_bar, tearoff=False)
        view_menu.add_command(label="大图预览 / 返回校对布局", command=self.toggle_image_focus)
        view_menu.add_checkbutton(label="显示槽位框", variable=self.overlay_var, command=self.refresh_image)
        view_menu.add_checkbutton(label="显示调整手柄", variable=self.slot_edit_var, command=self.refresh_image)
        view_menu.add_checkbutton(label="简洁人工核验模式", variable=self.simple_mode_var, command=self.apply_display_mode)
        view_menu.add_separator()
        view_menu.add_command(label="图片缩小", command=lambda: self.change_zoom(0.85))
        view_menu.add_command(label="适应窗口", command=self.reset_zoom)
        view_menu.add_command(label="原始清晰度 1:1", command=self.set_actual_size)
        view_menu.add_command(label="图片放大", command=lambda: self.change_zoom(1.18))
        view_menu.add_separator()
        view_menu.add_command(label="左转 90°", command=lambda: self.rotate_image(-90))
        view_menu.add_command(label="右转 90°", command=lambda: self.rotate_image(90))
        view_menu.add_command(label="恢复方向", command=self.reset_rotation)
        menu_bar.add_cascade(label="视图", menu=view_menu)

        help_menu = tk.Menu(menu_bar, tearoff=False)
        help_menu.add_command(label=f"关于卡牌校对器 v{EDITOR_VERSION}", command=lambda: messagebox.showinfo("关于", f"万境奇旅卡牌可视化人工校对器 v{EDITOR_VERSION}"))
        menu_bar.add_cascade(label="帮助", menu=help_menu)
        self.menu_bar = menu_bar
        self.configure(menu=menu_bar)

    def open_story_review_editor(self) -> None:
        """在独立非模态窗口打开故事 OCR 核验器，主卡牌编辑器保持可用。"""
        try:
            from story_review_visual_editor import StoryReviewEditor
        except Exception as exc:
            messagebox.showerror("故事核验器不可用", str(exc), parent=self)
            return
        if self.story_review_window is not None:
            try:
                if self.story_review_window.winfo_exists():
                    self.story_review_window.deiconify()
                    self.story_review_window.lift()
                    self.story_review_window.focus_force()
                    return
            except tk.TclError:
                pass
        review_path = PROJECT / ".codex-temp" / "storybooks_ocr_zh" / "story_review_entries_narrowed.json"
        if not review_path.exists():
            selected = filedialog.askopenfilename(
                title="选择故事文本人工核验 JSON",
                filetypes=[("故事核验 JSON", "story_review_entries_narrowed.json"), ("JSON 文件", "*.json"), ("全部文件", "*.*")],
                parent=self,
            )
            if not selected:
                return
            review_path = Path(selected)
        self.story_review_window = StoryReviewEditor(self, review_path)
        self.story_review_window.lift()

    def enable_text_undo(self, widget: tk.Widget) -> None:
        for child in widget.winfo_children():
            if isinstance(child, tk.Text):
                child.configure(undo=True, maxundo=-1, autoseparators=True)
            self.enable_text_undo(child)

    def remember_entry_value(self, event: tk.Event) -> None:
        widget = event.widget
        if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
            try:
                widget._audit_focus_value = widget.get()  # type: ignore[attr-defined]
            except tk.TclError:
                pass

    def handle_clipboard(self, event: tk.Event, virtual_event: str) -> str:
        try:
            event.widget.event_generate(virtual_event)
            if virtual_event in ("<<Paste>>", "<<Cut>>"):
                self.note_user_edit(event)
        except tk.TclError:
            pass
        return "break"

    def widget_is_in_editor(self, widget: tk.Widget) -> bool:
        return str(widget) == str(self.tabs) or str(widget).startswith(str(self.tabs) + ".")

    def note_user_edit(self, event: tk.Event) -> None:
        if not self.loading and self.current_number and self.widget_is_in_editor(event.widget):
            self.user_modified_current = True

    def note_user_key_edit(self, event: tk.Event) -> None:
        widget = event.widget
        if not isinstance(widget, (tk.Text, tk.Entry, ttk.Entry, ttk.Combobox)):
            return
        if event.state & 0x4:
            return
        if event.keysym in ("BackSpace", "Delete", "Return", "KP_Enter") or len(event.char or "") == 1:
            self.note_user_edit(event)

    def handle_undo(self, event: tk.Event) -> str:
        widget = event.widget
        if isinstance(widget, tk.Text):
            try:
                widget.edit_undo()
                return "break"
            except tk.TclError:
                pass
        if isinstance(widget, (tk.Entry, ttk.Entry, ttk.Combobox)) and hasattr(widget, "_audit_focus_value"):
            try:
                value = widget._audit_focus_value  # type: ignore[attr-defined]
                widget.delete(0, tk.END)
                widget.insert(0, value)
                return "break"
            except tk.TclError:
                pass
        self.undo_card_change()
        return "break"

    def handle_redo(self, _event: tk.Event) -> str:
        self.redo_card_change()
        return "break"

    def handle_copy_global(self, _event: tk.Event) -> str:
        self.copy_selected_slot()
        return "break"

    def handle_paste_global(self, _event: tk.Event) -> str:
        self.paste_selected_slot()
        return "break"

    def handle_cut_global(self, _event: tk.Event) -> str:
        if self.copy_selected_slot():
            if self.current_slot_index is not None:
                self.delete_challenge_slot()
            elif self.current_boost_index is not None:
                self.delete_boost_slot()
        return "break"

    def copy_selected_slot(self) -> bool:
        if not self.current_number:
            return False
        card = self.by_number[self.current_number]
        if self.current_slot_index is not None:
            slots = card.get("挑战骰", {}).get("槽位", [])
            if self.current_slot_index < len(slots):
                self.slot_clipboard = {"类型": "挑战骰槽", "数据": deepcopy(slots[self.current_slot_index])}
                self.status_var.set(f"已复制挑战骰槽 {self.current_slot_index + 1}，按 Ctrl+V 粘贴")
                return True
        if self.current_boost_index is not None:
            boxes = card.get("小卡", {}).get("强化容量", {}).get("槽位坐标", [])
            if self.current_boost_index < len(boxes):
                self.slot_clipboard = {"类型": "强化槽", "数据": deepcopy(boxes[self.current_boost_index])}
                self.status_var.set(f"已复制强化槽 {self.current_boost_index + 1}，按 Ctrl+V 粘贴")
                return True
        self.status_var.set("请先在卡图或槽位列表中选中一个槽位")
        return False

    def paste_selected_slot(self) -> None:
        if not self.current_number or not self.slot_clipboard:
            self.status_var.set("槽位剪贴板为空，请先选中槽位并按 Ctrl+C")
            return
        card = self.by_number[self.current_number]
        kind = self.slot_clipboard.get("类型")
        if kind == "挑战骰槽":
            self.push_undo(self.current_number, card)
            copied = deepcopy(self.slot_clipboard["数据"])
            bbox = list(copied.get("坐标_原图像素") or self.default_bbox(0.22))
            bbox = [float(value) + 35.0 for value in bbox]
            copied["坐标_原图像素"] = bbox
            slots = card.setdefault("挑战骰", {}).setdefault("槽位", [])
            insert_at = self.current_slot_index + 1 if self.current_slot_index is not None else len(slots)
            slots.insert(insert_at, copied)
            self.set_slot_bbox(card, insert_at, bbox)
            self.reindex_challenge_slots(card)
            self.log_slot_change(card, "粘贴", f"从槽位剪贴板粘贴为槽位 {insert_at + 1}")
            self.populate_slots(card)
            self.current_slot_index = None
            self.current_boost_index = None
            self.slot_tree.selection_set(str(insert_at))
            self.on_slot_select()
        elif kind == "强化槽":
            if not card.get("小卡", {}).get("是否小卡"):
                self.status_var.set("强化槽只能粘贴到小卡")
                return
            self.push_undo(self.current_number, card)
            bbox = [float(value) + 25.0 for value in deepcopy(self.slot_clipboard["数据"])]
            boxes = card.setdefault("小卡", {}).setdefault("强化容量", {}).setdefault("槽位坐标", [])
            insert_at = self.current_boost_index + 1 if self.current_boost_index is not None else len(boxes)
            boxes.insert(insert_at, bbox)
            self.sync_boost_slots(card)
            self.log_boost_change(card, "粘贴", f"从槽位剪贴板粘贴为强化槽 {insert_at + 1}")
            self.populate_boost_slots(card)
            self.current_slot_index = None
            self.current_boost_index = insert_at
            self.boost_tree.selection_set(str(insert_at))
            self.on_boost_select()
        self.dirty = True
        self.refresh_image()
        self.status_var.set(f"已粘贴{kind}，尚未写入磁盘")

    def push_undo(self, number: str, snapshot: dict[str, Any]) -> None:
        self.undo_stack.append((number, deepcopy(snapshot)))
        if len(self.undo_stack) > 60:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def replace_card(self, number: str, card: dict[str, Any]) -> None:
        self.by_number[number] = card
        for index, current in enumerate(self.cards):
            if str(current.get("编号")) == number:
                self.cards[index] = card
                return

    def undo_card_change(self) -> None:
        if not self.undo_stack:
            self.status_var.set("没有可撤回的卡牌修改")
            return
        number, previous = self.undo_stack.pop()
        current = self.by_number.get(number)
        if current is not None:
            self.redo_stack.append((number, deepcopy(current)))
        self.replace_card(number, previous)
        self.dirty = True
        if self.current_number == number:
            self.load_card(previous)
        self.status_var.set(f"已撤回卡牌 {number} 的上一步修改")

    def redo_card_change(self) -> None:
        if not self.redo_stack:
            self.status_var.set("没有可重做的卡牌修改")
            return
        number, restored = self.redo_stack.pop()
        current = self.by_number.get(number)
        if current is not None:
            self.undo_stack.append((number, deepcopy(current)))
        self.replace_card(number, restored)
        self.dirty = True
        if self.current_number == number:
            self.load_card(restored)
        self.status_var.set(f"已重做卡牌 {number} 的修改")

    def _build_card_list(self) -> None:
        frame = ttk.Frame(self, padding=(6, 0, 3, 6))
        self.card_list_frame = frame
        frame.grid(row=1, column=0, sticky="nsew")
        frame.rowconfigure(4, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="卡牌列表", font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=4)
        search = ttk.Entry(frame, textvariable=self.search_var, width=24)
        search.grid(row=1, column=0, sticky="ew", pady=3)
        search.bind("<KeyRelease>", lambda _e: self.filter_cards())
        type_box = ttk.Combobox(frame, textvariable=self.type_filter, values=["全部", "大卡", "小卡", "交锋卡"], state="readonly")
        type_box.grid(row=2, column=0, sticky="ew", pady=3)
        type_box.bind("<<ComboboxSelected>>", lambda _e: self.filter_cards())
        review_box = ttk.Combobox(
            frame,
            textvariable=self.review_filter,
            values=["全部", "待人工核验", "有未解决骰槽", "有人工修订", "AI建议优先", "待AI", "AI已处理", "有提示词", "未修订", "已修订未保存", "已修订", "未校对", "校对中", "已核验", "有问题"],
            state="readonly",
        )
        review_box.grid(row=3, column=0, sticky="ew", pady=3)
        review_box.bind("<<ComboboxSelected>>", lambda _e: self.filter_cards())
        list_wrap = ttk.Frame(frame)
        list_wrap.grid(row=4, column=0, sticky="nsew", pady=(4, 0))
        list_wrap.rowconfigure(0, weight=1)
        self.card_list = tk.Listbox(list_wrap, width=20, exportselection=False, font=("Microsoft YaHei UI", 10))
        self.card_list.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(list_wrap, orient="vertical", command=self.card_list.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.card_list.configure(yscrollcommand=sb.set)
        self.card_list.bind("<<ListboxSelect>>", self.on_card_select)
        self.queue_count_var = tk.StringVar(value="正在统计核验队列…")
        ttk.Label(frame, textvariable=self.queue_count_var, foreground="#38556b", wraplength=215, justify="left").grid(row=5, column=0, sticky="ew", pady=(5, 0))

    def _build_image_panel(self) -> None:
        frame = ttk.Frame(self, padding=(3, 0, 3, 6))
        self.image_frame = frame
        frame.grid(row=1, column=1, sticky="nsew")
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        self.image_title = ttk.Label(frame, text="卡图", anchor="center", font=("Microsoft YaHei UI", 11, "bold"))
        self.image_title.grid(row=0, column=0, sticky="ew", pady=4)
        wrap = ttk.Frame(frame)
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(wrap, bg="#202226", highlightthickness=1, highlightbackground="#555")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        xsb = ttk.Scrollbar(wrap, orient="horizontal", command=self.canvas.xview)
        ysb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        xsb.grid(row=1, column=0, sticky="ew")
        ysb.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(xscrollcommand=xsb.set, yscrollcommand=ysb.set)
        self.canvas.bind("<Configure>", lambda _e: self.refresh_image())
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<ButtonPress-1>", self.on_image_press)
        self.canvas.bind("<B1-Motion>", self.on_image_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_image_release)

    def _build_editor_tabs(self) -> None:
        frame = ttk.Frame(self, padding=(3, 0, 6, 6))
        self.editor_frame = frame
        frame.grid(row=1, column=2, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.tabs = ttk.Notebook(frame)
        self.tabs.grid(row=0, column=0, sticky="nsew")
        self._build_checklist_tab()
        self._build_basic_tab()
        self._build_map_tab()
        self._build_actions_tab()
        self._build_slots_tab()
        self._build_small_tab()
        self._build_review_tab()
        self._build_advanced_tab()
        self.apply_display_mode()

    def _build_checklist_tab(self) -> None:
        tab = self._scroll_tab("人工核验清单")
        ttk.Label(
            tab,
            text="只需要对照左侧卡图核对本页列出的内容。坐标、置信度、来源UID等机器字段默认隐藏，但仍完整保存在文件中。",
            foreground="#174f2a",
            wraplength=520,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(2, 8))
        self.checklist_status_var = tk.StringVar(value="请选择卡牌")
        ttk.Label(tab, textvariable=self.checklist_status_var, font=("Microsoft YaHei UI", 10, "bold"), foreground="#8a3f00", wraplength=520, justify="left").grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 5))
        self.checklist_text = tk.Text(tab, width=44, height=29, wrap="word", bg="#f4faf5", padx=8, pady=8)
        self.checklist_text.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6)
        self.checklist_text.configure(state="disabled")
        buttons = ttk.Frame(tab)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", padx=6, pady=8)
        ttk.Button(buttons, text="骰槽/框位有错", command=lambda: self.open_editor_tab("挑战骰槽")).pack(side="left", padx=3)
        ttk.Button(buttons, text="写纠正提示", command=self.focus_ai_correction).pack(side="left", padx=3)
        ttk.Button(buttons, text="这张卡已核对无误", command=self.mark_verified).pack(side="left", padx=3)

    def apply_display_mode(self) -> None:
        """默认只露出当前卡型需要核对的页面；完整数据从不被删除。"""
        if not hasattr(self, "tabs"):
            return
        simple = bool(self.simple_mode_var.get())
        card = self.by_number.get(self.current_number or "", {})
        visible = {"人工核验清单", "基础资料", "总结与AI纠正"}
        if card:
            is_location = bool(card.get("地图", {}).get("是否地点牌", False))
            if is_location:
                visible.update({"地图与罗盘", "事件与行动"})
            else:
                # 小卡即使当前识别为0个骰槽，也必须能进入该页补回漏槽。
                visible.update({"挑战骰槽", "小卡/强化/技能"})
                map_data = card.get("地图", {})
                if any(map_data.get(key) for key in ("地点行动", "图画内地点行动")):
                    visible.add("事件与行动")
        for title, page in self.tab_pages.items():
            state = "normal" if (not simple or title in visible) else "hidden"
            try:
                self.tabs.tab(page, state=state)
            except tk.TclError:
                pass
        if hasattr(self, "slot_tree"):
            self.slot_tree.column("confidence", width=0 if simple else 65, minwidth=0, stretch=not simple)

    def open_editor_tab(self, title: str) -> None:
        page = self.tab_pages.get(title)
        if page is None:
            return
        self.tabs.tab(page, state="normal")
        self.tabs.select(page)

    def focus_ai_correction(self) -> None:
        self.open_editor_tab("总结与AI纠正")
        self.review_ai_prompt.focus_set()
        self.review_ai_prompt.see("end")

    def _build_status_bar(self, parent: tk.Misc) -> ttk.Frame:
        bar = ttk.Frame(parent, padding=(6, 4))
        ttk.Label(bar, text="标记当前卡牌：").pack(side="left", padx=(0, 5))
        for label, status in (("未修订", "未修订"), ("已核验", "已核验")):
            ttk.Button(bar, text=f"标记为{label}", command=lambda value=status: self.mark_review_status(value)).pack(side="left", padx=2)
        return bar

    def _prepend_status_bar(self, tab: ttk.Frame) -> None:
        """给非滚动标签页顶部插入统一状态栏，并平移已有网格控件。"""
        columns, rows = tab.grid_size()
        weights = [tab.grid_rowconfigure(row).get("weight", 0) for row in range(rows)]
        for child in tab.grid_slaves():
            info = child.grid_info()
            child.grid_configure(row=int(info.get("row", 0)) + 1)
        for row, weight in enumerate(weights):
            tab.grid_rowconfigure(row + 1, weight=weight)
        tab.grid_rowconfigure(0, weight=0)
        bar = self._build_status_bar(tab)
        bar.grid(row=0, column=0, columnspan=max(1, columns), sticky="ew")

    def _scroll_tab(self, title: str) -> ttk.Frame:
        outer = ttk.Frame(self.tabs)
        self.tabs.add(outer, text=title)
        self.tab_pages[title] = outer
        outer.rowconfigure(0, weight=0)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)
        self._build_status_bar(outer).grid(row=0, column=0, columnspan=2, sticky="ew")
        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, padding=8)
        inner.columnconfigure(1, weight=1)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")
        return inner

    def _build_basic_tab(self) -> None:
        tab = self._scroll_tab("基础资料")
        self.base_number = tk.StringVar()
        self.base_type = tk.StringVar()
        self.base_subtype = tk.StringVar()
        self.base_detected_name = tk.StringVar()
        self.base_name = tk.StringVar()
        self.base_value = tk.StringVar()
        self.base_texture = tk.StringVar()
        add_entry(tab, 0, "编号", self.base_number, readonly=True)
        add_entry(tab, 1, "卡牌类型", self.base_type, readonly=True)
        add_entry(tab, 2, "小卡类型", self.base_subtype, readonly=True)
        add_entry(tab, 3, "小卡识别名称", self.base_detected_name, readonly=True)
        self.base_name_entry = add_entry(tab, 4, "小卡人工校对名称", self.base_name)
        self.base_value_combo = add_combo(tab, 5, "价格（彩色数字标记）", self.base_value, NUMBER_CHOICES)
        add_entry(tab, 6, "贴图路径", self.base_texture, readonly=True)
        ttk.Label(tab, text="识别到的基础文本").grid(row=7, column=0, columnspan=2, sticky="w", padx=6, pady=(10, 3))
        self.base_detected_text = tk.Text(tab, width=44, height=12, wrap="word", bg="#f0f0f0")
        self.base_detected_text.grid(row=8, column=0, columnspan=2, sticky="nsew", padx=6)
        self.base_detected_text.configure(state="disabled")
        ttk.Label(tab, text="人工修订基础文本").grid(row=9, column=0, columnspan=2, sticky="w", padx=6, pady=(10, 3))
        self.base_text = tk.Text(tab, width=44, height=12, wrap="word")
        self.base_text.grid(row=10, column=0, columnspan=2, sticky="nsew", padx=6)
        ttk.Button(tab, text="应用本页修改", command=self.commit_current).grid(row=11, column=1, sticky="e", padx=6, pady=8)

    def _build_map_tab(self) -> None:
        tab = self._scroll_tab("地图与罗盘")
        self.map_terrain = tk.StringVar()
        self.map_region = tk.StringVar()
        self.map_marker = tk.StringVar()
        self.map_terrain_combo = add_combo(tab, 0, "地形主类型", self.map_terrain, [""])
        ttk.Label(tab, text="地形标签（可多选）").grid(row=1, column=0, sticky="nw", padx=6, pady=4)
        self.map_tags_list = tk.Listbox(tab, selectmode="multiple", exportselection=False, height=7)
        self.map_tags_list.grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        self.map_tags_list.bind("<<ListboxSelect>>", self.note_user_edit)
        self.map_region_combo = add_combo(tab, 2, "地区标记", self.map_region, [""])
        add_entry(tab, 3, "元素/区域标记（复杂结构保留文本）", self.map_marker)
        ttk.Separator(tab).grid(row=4, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(tab, text="上下左右道路罗盘", font=("Microsoft YaHei UI", 10, "bold")).grid(row=5, column=0, columnspan=2, sticky="w", padx=6)
        self.road_vars: dict[str, dict[str, tk.StringVar]] = {}
        row = 6
        for key, label in DIR_KEYS:
            box = ttk.LabelFrame(tab, text=label, padding=5)
            box.grid(row=row, column=0, columnspan=2, sticky="ew", padx=6, pady=4)
            for col in range(8):
                box.columnconfigure(col, weight=1 if col in (1, 3, 5, 7) else 0)
            values = {name: tk.StringVar() for name in ("state", "printed", "destination", "cost")}
            self.road_vars[key] = values
            ttk.Label(box, text="道路").grid(row=0, column=0)
            ttk.Combobox(box, textvariable=values["state"], values=ROAD_STATES, width=18, state="readonly").grid(row=0, column=1, sticky="ew", padx=3)
            ttk.Label(box, text="印刷值").grid(row=0, column=2)
            ttk.Entry(box, textvariable=values["printed"], width=8).grid(row=0, column=3, sticky="ew", padx=3)
            ttk.Label(box, text="目标编号").grid(row=0, column=4)
            ttk.Entry(box, textvariable=values["destination"], width=8).grid(row=0, column=5, sticky="ew", padx=3)
            ttk.Label(box, text="花费").grid(row=0, column=6)
            ttk.Combobox(box, textvariable=values["cost"], values=COST_CHOICES, width=5, state="readonly").grid(row=0, column=7, sticky="ew", padx=3)
            row += 1
        ttk.Button(tab, text="应用地图与罗盘修改", command=self.commit_current).grid(row=row, column=1, sticky="e", padx=6, pady=8)

    def _build_actions_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=7)
        self.tabs.add(tab, text="事件与行动")
        self.tab_pages["事件与行动"] = tab
        tab.rowconfigure(1, weight=1)
        tab.rowconfigure(3, weight=2)
        tab.columnconfigure(0, weight=1)
        action_toolbar = ttk.Frame(tab)
        action_toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Label(action_toolbar, text="抵达事件、地点行动与图画内行动").pack(side="left")
        ttk.Button(action_toolbar, text="新增地点行动", command=lambda: self.add_action("地点行动")).pack(side="right", padx=2)
        ttk.Button(action_toolbar, text="新增图画内行动", command=lambda: self.add_action("图画内地点行动")).pack(side="right", padx=2)
        ttk.Button(action_toolbar, text="新增抵达事件", command=self.add_arrival_event).pack(side="right", padx=2)
        ttk.Button(action_toolbar, text="复制所选", command=self.duplicate_action).pack(side="right", padx=2)
        ttk.Button(action_toolbar, text="删除所选", command=self.delete_action).pack(side="right", padx=2)
        self.action_tree = ttk.Treeview(tab, columns=("kind", "text", "book", "entry", "cost", "repeatable", "status"), show="headings", height=9)
        for col, label, width in [("kind", "类型", 95), ("text", "行动/事件", 220), ("book", "故事书", 90), ("entry", "条目", 60), ("cost", "花费", 50), ("repeatable", "始终可用", 75), ("status", "状态", 85)]:
            self.action_tree.heading(col, text=label)
            self.action_tree.column(col, width=width, anchor="w")
        self.action_tree.grid(row=1, column=0, sticky="nsew", pady=4)
        self.action_tree.bind("<<TreeviewSelect>>", self.on_action_select)
        form = ttk.LabelFrame(tab, text="所选事件/行动", padding=6)
        form.grid(row=2, column=0, sticky="ew", pady=4)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        self.action_kind = tk.StringVar()
        self.action_text = tk.StringVar()
        self.action_family = tk.StringVar()
        self.action_book = tk.StringVar()
        self.action_entry = tk.StringVar()
        self.action_cost = tk.StringVar()
        self.action_title = tk.StringVar()
        self.action_status = tk.StringVar()
        self.action_repeatable = tk.BooleanVar(value=False)
        self.action_usage_evidence = tk.StringVar()
        controls = [
            ("类型", ttk.Entry(form, textvariable=self.action_kind, state="readonly")),
            ("行动文字", ttk.Entry(form, textvariable=self.action_text)),
            ("行动家族", ttk.Combobox(form, textvariable=self.action_family, values=ACTION_FAMILIES, state="readonly")),
            ("故事书", ttk.Combobox(form, textvariable=self.action_book, values=STORY_BOOKS, state="readonly")),
            ("条目", ttk.Entry(form, textvariable=self.action_entry)),
            ("花费", ttk.Combobox(form, textvariable=self.action_cost, values=COST_CHOICES, state="readonly")),
            ("事件标题", ttk.Entry(form, textvariable=self.action_title)),
            ("执行状态", ttk.Combobox(form, textvariable=self.action_status, values=EXECUTION_STATES, state="readonly")),
            ("规则依据", ttk.Entry(form, textvariable=self.action_usage_evidence)),
        ]
        for i, (label, widget) in enumerate(controls):
            r, c = divmod(i, 2)
            ttk.Label(form, text=label).grid(row=r, column=c * 2, sticky="w", padx=3, pady=2)
            widget.grid(row=r, column=c * 2 + 1, sticky="ew", padx=3, pady=2)
        self.action_repeatable_check = ttk.Checkbutton(form, text="始终可用（可重复）", variable=self.action_repeatable)
        self.action_repeatable_check.grid(row=5, column=0, columnspan=2, sticky="w", padx=3, pady=2)
        detail = ttk.Panedwindow(tab, orient="horizontal")
        detail.grid(row=3, column=0, sticky="nsew", pady=4)
        left = ttk.LabelFrame(detail, text="中文事件正文", padding=4)
        right = ttk.LabelFrame(detail, text="结构化效果与选项（可读摘要）", padding=4)
        detail.add(left, weight=1)
        detail.add(right, weight=1)
        self.action_event_text = tk.Text(left, width=30, wrap="word")
        self.action_event_text.pack(fill="both", expand=True)
        self.action_structure = tk.Text(right, width=30, wrap="word", bg="#f0f0f0")
        self.action_structure.pack(fill="both", expand=True)
        self.action_structure.configure(state="disabled")
        ttk.Button(tab, text="应用所选事件/行动修改", command=self.apply_action).grid(row=4, column=0, sticky="e", pady=5)
        self._prepend_status_bar(tab)

    def _build_slots_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=7)
        self.tabs.add(tab, text="挑战骰槽")
        self.tab_pages["挑战骰槽"] = tab
        tab.rowconfigure(2, weight=1)
        tab.columnconfigure(0, weight=1)
        slot_toolbar = ttk.Frame(tab)
        slot_toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Label(slot_toolbar, text="点击槽位后，卡图中对应槽会以红框高亮").pack(side="left")
        ttk.Button(slot_toolbar, text="新增骰槽", command=self.add_challenge_slot).pack(side="right", padx=2)
        ttk.Button(slot_toolbar, text="复制骰槽", command=self.duplicate_challenge_slot).pack(side="right", padx=2)
        ttk.Button(slot_toolbar, text="删除所选骰槽", command=self.delete_challenge_slot).pack(side="right", padx=2)
        ttk.Label(
            tab,
            text="判定规则：只有完整留白方框才是骰槽；主动/被动技能文字块、圆形效果和彩色行动列表都不是骰槽。",
            foreground="#8a3b00",
            wraplength=860,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(5, 1))
        self.slot_tree = ttk.Treeview(tab, columns=("index", "type", "skill", "result", "ability", "cost", "gain", "impact", "confidence"), show="headings", height=12)
        for col, label, width in [("index", "序号", 42), ("type", "槽位类型", 120), ("skill", "技能", 80), ("result", "结果", 70), ("ability", "能力要求", 110), ("cost", "花费", 45), ("gain", "生成", 45), ("impact", "闪电", 45), ("confidence", "置信度", 65)]:
            self.slot_tree.heading(col, text=label)
            self.slot_tree.column(col, width=width, anchor="center")
        self.slot_tree.grid(row=2, column=0, sticky="nsew", pady=4)
        self.slot_tree.bind("<<TreeviewSelect>>", self.on_slot_select)
        form = ttk.LabelFrame(tab, text="所选骰槽", padding=7)
        form.grid(row=3, column=0, sticky="ew")
        for col in (1, 3, 5):
            form.columnconfigure(col, weight=1)
        self.slot_type = tk.StringVar()
        self.slot_skill = tk.StringVar()
        self.slot_color = tk.StringVar()
        self.slot_result = tk.StringVar()
        self.slot_terrain = tk.StringVar()
        self.slot_interaction = tk.BooleanVar()
        self.slot_cost = tk.StringVar()
        self.slot_gain = tk.StringVar()
        self.slot_impact = tk.BooleanVar()
        self.slot_modifier = tk.StringVar()
        self.slot_action = tk.StringVar()
        controls = [
            ("槽位类型", ttk.Combobox(form, textvariable=self.slot_type, values=SLOT_TYPES, state="readonly")),
            ("技能类型", ttk.Combobox(form, textvariable=self.slot_skill, values=SKILLS, state="readonly")),
            ("技能颜色", ttk.Combobox(form, textvariable=self.slot_color, values=COLORS, state="readonly")),
            ("骰面结果", ttk.Combobox(form, textvariable=self.slot_result, values=RESULTS, state="readonly")),
            ("能力要求（可多选）", tk.Listbox(form, selectmode="multiple", exportselection=False, height=4)),
            ("地形要求", ttk.Combobox(form, textvariable=self.slot_terrain, values=["未指定/未识别"], state="readonly")),
            ("强化花费", ttk.Combobox(form, textvariable=self.slot_cost, values=NUMBER_CHOICES, state="readonly")),
            ("强化生成", ttk.Combobox(form, textvariable=self.slot_gain, values=NUMBER_CHOICES, state="readonly")),
            ("额外投骰", ttk.Combobox(form, textvariable=self.slot_modifier, values=NUMBER_CHOICES, state="readonly")),
            ("特定行动", ttk.Combobox(form, textvariable=self.slot_action, values=[""], state="readonly")),
        ]
        self.slot_ability_list = controls[4][1]
        self.slot_terrain_combo = controls[5][1]
        self.slot_action_combo = controls[9][1]
        for value in SKILLS[1:]:
            self.slot_ability_list.insert(tk.END, value)
        self.slot_ability_list.bind("<<ListboxSelect>>", self.note_user_edit)
        for i, (label, widget) in enumerate(controls):
            r, c = divmod(i, 3)
            ttk.Label(form, text=label).grid(row=r * 2, column=c * 2, sticky="w", padx=3, pady=(3, 0))
            widget.grid(row=r * 2 + 1, column=c * 2, columnspan=2, sticky="ew", padx=3, pady=(0, 3))
        ttk.Checkbutton(form, text="互动元素通配槽", variable=self.slot_interaction).grid(row=8, column=0, sticky="w", padx=3)
        ttk.Checkbutton(form, text="有闪电标志", variable=self.slot_impact).grid(row=8, column=2, sticky="w", padx=3)
        self.slot_coords = tk.StringVar()
        ttk.Label(form, text="卡面坐标 [x1,y1,x2,y2]（固定334×327，只可移动）").grid(row=9, column=0, sticky="w", padx=3)
        ttk.Entry(form, textvariable=self.slot_coords).grid(row=9, column=1, columnspan=5, sticky="ew", padx=3)
        ttk.Button(form, text="应用所选骰槽修改", command=self.apply_slot).grid(row=10, column=4, columnspan=2, sticky="e", pady=5)
        self._prepend_status_bar(tab)

    def _build_small_tab(self) -> None:
        tab = self._scroll_tab("小卡/强化/技能")
        self.small_type = tk.StringVar()
        self.small_reserve = tk.StringVar()
        self.small_value = tk.StringVar()
        self.small_initial = tk.StringVar()
        self.small_capacity = tk.StringVar()
        self.small_type_combo = add_combo(tab, 0, "小卡类型", self.small_type, SMALL_TYPES)
        add_combo(tab, 1, "文字规则储备上限（通常不适用）", self.small_reserve, NUMBER_CHOICES)
        add_combo(tab, 2, "价格（彩色数字标记）", self.small_value, NUMBER_CHOICES)
        add_combo(tab, 3, "放置时强化点数", self.small_initial, NUMBER_CHOICES)
        add_combo(tab, 4, "强化储备数（灰色方格）", self.small_capacity, NUMBER_CHOICES)
        boost_box = ttk.LabelFrame(tab, text="强化储备格（卡面灰色方格；图中黄色框）", padding=6)
        boost_box.grid(row=5, column=0, columnspan=2, sticky="ew", padx=6, pady=(8, 2))
        boost_box.columnconfigure(0, weight=1)
        self.boost_tree = ttk.Treeview(boost_box, columns=("index", "bbox"), show="headings", height=5)
        self.boost_tree.heading("index", text="序号")
        self.boost_tree.heading("bbox", text="卡面坐标 [x1,y1,x2,y2]")
        self.boost_tree.column("index", width=55, anchor="center")
        self.boost_tree.column("bbox", width=360, anchor="w")
        self.boost_tree.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.boost_tree.bind("<<TreeviewSelect>>", self.on_boost_select)
        self.boost_coords = tk.StringVar()
        ttk.Entry(boost_box, textvariable=self.boost_coords).grid(row=1, column=0, sticky="ew", pady=5)
        ttk.Button(boost_box, text="应用坐标", command=self.apply_boost_coords).grid(row=1, column=1, padx=3)
        ttk.Button(boost_box, text="新增强化槽", command=self.add_boost_slot).grid(row=2, column=0, sticky="w", pady=3)
        ttk.Button(boost_box, text="删除所选强化槽", command=self.delete_boost_slot).grid(row=2, column=1, sticky="w", padx=3, pady=3)
        ttk.Label(tab, text="放置效果（每行一项）").grid(row=6, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 2))
        self.small_placement = tk.Text(tab, width=44, height=7, wrap="word")
        self.small_placement.grid(row=7, column=0, columnspan=2, sticky="ew", padx=6)
        ttk.Label(tab, text="技能效果/强化异能（可读文本）").grid(row=8, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 2))
        self.small_skills = tk.Text(tab, width=44, height=10, wrap="word")
        self.small_skills.grid(row=9, column=0, columnspan=2, sticky="ew", padx=6)
        ttk.Label(tab, text="技能花销（每行一项）").grid(row=10, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 2))
        self.small_costs = tk.Text(tab, width=44, height=7, wrap="word")
        self.small_costs.grid(row=11, column=0, columnspan=2, sticky="ew", padx=6)
        ttk.Button(tab, text="应用小卡/强化修改", command=self.commit_current).grid(row=12, column=1, sticky="e", padx=6, pady=8)

    def _build_review_tab(self) -> None:
        tab = self._scroll_tab("总结与AI纠正")
        self.review_status = tk.StringVar(value="未校对")
        ttk.Label(tab, text="总校对状态").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(tab, textvariable=self.review_status, values=["未校对", "校对中", "已核验", "有问题"], state="readonly").grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        ttk.Label(tab, text="牌面总结（覆盖全部大卡、小卡与交锋卡）", foreground="#24527a").grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 2))
        self.review_summary = tk.Text(tab, width=44, height=20, wrap="word", bg="#edf5fb")
        self.review_summary.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6)
        self.review_summary.configure(state="disabled")
        summary_buttons = ttk.Frame(tab)
        summary_buttons.grid(row=3, column=0, columnspan=2, sticky="e", padx=6, pady=5)
        ttk.Button(summary_buttons, text="按当前数据重新生成", command=self.regenerate_current_summary).pack(side="left", padx=4)
        ttk.Button(summary_buttons, text="导出全部总结", command=self.export_all_card_summaries).pack(side="left", padx=4)
        ttk.Label(tab, text="问题列表（每行一项）").grid(row=4, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 2))
        self.review_issues = tk.Text(tab, width=44, height=9, wrap="word")
        self.review_issues.grid(row=5, column=0, columnspan=2, sticky="ew", padx=6)
        ttk.Label(tab, text="校对备注").grid(row=6, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 2))
        self.review_notes = tk.Text(tab, width=44, height=8, wrap="word")
        self.review_notes.grid(row=7, column=0, columnspan=2, sticky="ew", padx=6)
        ttk.Label(
            tab,
            text="纠正提示词（直接写事实纠正；例如：时间结果是红色不是橙色。图示表示花费2点强化后可以获得一个蓝色标记。）",
            foreground="#8a3f00",
            wraplength=520,
            justify="left",
        ).grid(row=8, column=0, columnspan=2, sticky="w", padx=6, pady=(8, 2))
        self.review_ai_prompt = tk.Text(tab, width=44, height=10, wrap="word", bg="#fff8dc")
        self.review_ai_prompt.grid(row=9, column=0, columnspan=2, sticky="ew", padx=6)
        self.review_ai_status = tk.StringVar(value="未填写提示词")
        ttk.Label(tab, textvariable=self.review_ai_status, foreground="#6b4b00").grid(row=10, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 0))
        buttons = ttk.Frame(tab)
        buttons.grid(row=11, column=0, columnspan=2, sticky="e", padx=6, pady=8)
        ttk.Button(buttons, text="导出待AI任务包", command=self.export_ai_prompt_cards).pack(side="left", padx=4)
        ttk.Button(buttons, text="标记为未修订", command=self.mark_current_unrevised).pack(side="left", padx=4)
        ttk.Button(buttons, text="标记为已核验", command=self.mark_verified).pack(side="left", padx=4)
        ttk.Button(buttons, text="应用校对结论", command=self.commit_current).pack(side="left", padx=4)

    def _build_advanced_tab(self) -> None:
        tab = ttk.Frame(self.tabs, padding=7)
        self.tabs.add(tab, text="高级原始JSON")
        self.tab_pages["高级原始JSON"] = tab
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)
        ttk.Label(tab, text="普通校对无需使用本页；仅处理表单无法覆盖的罕见结构。", foreground="#9b4d00").grid(row=0, column=0, sticky="w")
        self.advanced_text = tk.Text(tab, width=44, wrap="none", font=("Consolas", 9), undo=True)
        self.advanced_text.grid(row=1, column=0, sticky="nsew", pady=5)
        buttons = ttk.Frame(tab)
        buttons.grid(row=2, column=0, sticky="e")
        ttk.Button(buttons, text="从表单刷新JSON", command=self.refresh_advanced).pack(side="left", padx=3)
        ttk.Button(buttons, text="应用高级JSON", command=self.apply_advanced).pack(side="left", padx=3)
        self._prepend_status_bar(tab)

    def load_document_async(self, path: Path, remote_sync: dict[str, Any] | None = None) -> None:
        self.document_load_generation += 1
        generation = self.document_load_generation
        self.document_ready = False
        self.status_var.set(f"正在加载 {path.name}…")

        def worker() -> None:
            try:
                document = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(document, dict) or not isinstance(document.get("卡牌"), list):
                    raise ValueError("文件缺少“卡牌”数组")
                self.after(0, lambda: self.apply_document_data(path, document, remote_sync, generation))
            except Exception as exc:
                detail = str(exc)
                self.after(0, lambda detail=detail: messagebox.showerror("打开失败", f"无法读取：{path}\n\n{detail}"))

        threading.Thread(target=worker, name="card-audit-load-document", daemon=True).start()

    def apply_document_data(
        self,
        path: Path,
        document: dict[str, Any],
        remote_sync: dict[str, Any] | None = None,
        generation: int | None = None,
        synchronous: bool = False,
    ) -> None:
        if generation is not None and generation != self.document_load_generation:
            return
        self.document = document
        self.cards = self.document.get("卡牌", [])
        self.summary_migration_count = 0
        for card in self.cards:
            card_id = int(card.get("编号", 0) or 0)
            if ensure_card_summary(card, self.abilities_by_card.get(card_id, [])):
                self.summary_migration_count += 1
        if self.summary_migration_count:
            self.document.setdefault("字段说明", {})["牌面总结"] = "面向人工校对的自动摘要；发现错误时在人工校对.待AI处理提示词中写明纠正。"
            self.dirty = True
        self.by_number = {str(card.get("编号")): card for card in self.cards}
        self.path = path
        self.remote_sync = remote_sync
        self.pending_revision_numbers.clear()
        # 先把窗口交还给 Tk，避免 111MB 文档的索引和首张大图缩放阻塞首屏。
        self.status_var.set(f"已读取 {len(self.cards)} 张卡，正在建立索引…")
        if synchronous:
            self.finish_document_load(generation, synchronous=True)
        else:
            self.after_idle(lambda: self.finish_document_load(generation))

    def finish_document_load(self, generation: int | None = None, synchronous: bool = False) -> None:
        if generation is not None and generation != self.document_load_generation:
            return
        self.refresh_choice_catalogs()
        self.populate_list()
        self.document_ready = True
        migration = f"｜已补全{self.summary_migration_count}份牌面总结，保存后写入" if self.summary_migration_count else ""
        self.status_var.set(f"已加载 {len(self.cards)} 张卡｜表单校对模式{migration}")
        if self.cards:
            self.card_list.selection_set(0)
            # 首张卡图再延后一帧，确保列表和窗口先显示出来。
            if synchronous:
                self.on_card_select()
            else:
                self.after_idle(self.on_card_select)

    def load_document(self, path: Path, remote_sync: dict[str, Any] | None = None) -> None:
        """同步接口仅供已在后台读取完成的旧调用；普通打开使用异步版本。"""
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(document, dict) or not isinstance(document.get("卡牌"), list):
                raise ValueError("文件缺少“卡牌”数组")
        except Exception as exc:
            messagebox.showerror("打开失败", f"无法读取：{path}\n\n{exc}")
            return
        self.apply_document_data(path, document, remote_sync, self.document_load_generation)

    def load_remote(self) -> None:
        if not self.remote_sync:
            self.configure_remote()
            return
        if self.dirty:
            answer = messagebox.askyesnocancel("有未保存修改", "重新加载远程文件会丢弃当前未保存修改，是否继续？")
            if answer is not True:
                return
        config_url = str(self.remote_sync.get("config_url") or "").strip()
        if not config_url:
            messagebox.showerror("远程配置无效", "缺少远程配置 URL。")
            return
        code = str(self.remote_sync.get("session_code") or "").strip() or read_saved_security_code()
        if not code:
            settings = ask_remote_settings(self, config_url)
            if not settings:
                return
            config_url, code = settings
            self.remote_sync["config_url"] = config_url
        self.status_var.set("正在从远程加载校对文件…")

        def worker() -> None:
            try:
                path, binding, document = load_remote_document(config_url, code)
                self.after(0, lambda: self.apply_remote_document(path, binding, document))
            except HTTPError as exc:
                if exc.code == 401:
                    self.after(0, lambda: self.retry_remote_auth(config_url))
                    return
                detail = f"HTTP {exc.code}: {exc.reason}"
                self.after(0, lambda detail=detail: self.handle_remote_error("远程加载失败", detail))
            except Exception as exc:
                detail = str(exc)
                self.after(0, lambda detail=detail: self.handle_remote_error("远程加载失败", detail))

        threading.Thread(target=worker, name="card-audit-remote-load", daemon=True).start()

    def retry_remote_auth(self, config_url: str) -> None:
        """旧安全码失效时清空预填值，要求重新输入后只重试一次。"""
        settings = ask_remote_settings(self, config_url, use_saved_code=False)
        if not settings:
            self.status_var.set("远程加载已取消")
            return
        retry_url, retry_code = settings
        self.remote_sync = {"config_url": retry_url}
        self.status_var.set("正在使用新安全码重试远程加载…")

        def worker() -> None:
            try:
                path, binding, document = load_remote_document(retry_url, retry_code)
                save_remote_config_url(retry_url)
                save_security_code(retry_code)
                self.after(0, lambda: self.apply_remote_document(path, binding, document))
            except HTTPError as exc:
                detail = "安全码不正确或服务器拒绝访问（HTTP 401）。请确认安全码后重试。" if exc.code == 401 else f"HTTP {exc.code}: {exc.reason}"
                self.after(0, lambda detail=detail: self.handle_remote_error("远程加载失败", detail))
            except Exception as exc:
                detail = str(exc)
                self.after(0, lambda detail=detail: self.handle_remote_error("远程加载失败", detail))

        threading.Thread(target=worker, name="card-audit-remote-auth-retry", daemon=True).start()

    def configure_remote(self) -> None:
        current = str(self.remote_sync.get("config_url") if self.remote_sync else "")
        settings = ask_remote_settings(self, current)
        if not settings:
            return
        config_url, code = settings
        self.status_var.set("正在验证远程配置并加载校对文件…")

        def worker() -> None:
            try:
                path, binding, document = load_remote_document(config_url, code)
                save_remote_config_url(config_url)
                save_security_code(code)
                self.after(0, lambda: self.apply_remote_document(path, binding, document))
            except HTTPError as exc:
                detail = "安全码不正确或服务器拒绝访问（HTTP 401）。请确认安全码后重试。" if exc.code == 401 else f"HTTP {exc.code}: {exc.reason}"
                self.after(0, lambda detail=detail: self.handle_remote_error("远程配置失败", detail))
            except Exception as exc:
                detail = str(exc)
                self.after(0, lambda detail=detail: self.handle_remote_error("远程配置失败", detail))

        threading.Thread(target=worker, name="card-audit-remote-config", daemon=True).start()

    def apply_remote_document(self, path: Path, binding: dict[str, Any], document: dict[str, Any]) -> None:
        if str(binding.get("session_code") or "").strip():
            save_security_code(str(binding["session_code"]))
        self.apply_document_data(path, document, binding)
        self.status_var.set(f"已从远程加载 {len(self.cards)} 张卡｜可开始校对")

    def sync_remote(self, notify: bool = True) -> None:
        if not self.remote_sync:
            self.configure_remote()
            return
        upload_url = str(self.remote_sync.get("upload_url") or "").strip()
        if not upload_url:
            messagebox.showerror("远程配置只读", "配置中没有 upload_url/上传URL，当前只能读取，无法上传。")
            return
        if self.remote_sync_running:
            self.status_var.set("远程同步正在进行，请稍候…")
            return
        self.commit_current()
        self.document["卡牌"] = self.cards
        payload = (json.dumps(self.document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        binding = dict(self.remote_sync)
        self.remote_sync_running = True
        self.status_var.set("正在上传并同步远程校对文件…")

        def worker() -> None:
            try:
                headers = remote_auth_headers(binding)
                headers["Content-Type"] = "application/json; charset=utf-8"
                etag = str(binding.get("etag") or "").strip()
                if etag:
                    headers["If-Match"] = etag
                request = urllib.request.Request(
                    upload_url,
                    data=payload,
                    headers=headers,
                    method=str(binding.get("method") or "PUT").upper(),
                )
                with urllib.request.urlopen(request, timeout=120, context=build_ssl_context()) as response:
                    new_etag = response.headers.get("ETag") or response.headers.get("Etag") or etag
                self.after(0, lambda: self.handle_remote_success(new_etag, notify))
            except HTTPError as exc:
                if exc.code == 412:
                    detail = "远程文件已被其他设备修改，本次上传已阻止。请先点击“加载远程”，确认差异后再提交。"
                else:
                    detail = f"HTTP {exc.code}: {exc.reason}"
                self.after(0, lambda detail=detail: self.handle_remote_error("远程同步失败", detail, notify))
            except Exception as exc:
                detail = str(exc)
                self.after(0, lambda detail=detail: self.handle_remote_error("远程同步失败", detail, notify))

        threading.Thread(target=worker, name="card-audit-remote-sync", daemon=True).start()

    def handle_remote_success(self, etag: str, notify: bool) -> None:
        self.remote_sync_running = False
        if self.remote_sync is not None:
            self.remote_sync["etag"] = etag
        self.status_var.set("远程校对文件已同步｜其他设备可加载最新内容")
        if notify:
            messagebox.showinfo("远程同步完成", "校对 JSON 已上传；卡图和本地资源未上传。")
        if self.closing:
            self.destroy()

    def handle_remote_error(self, title: str, detail: str, notify: bool = True) -> None:
        self.remote_sync_running = False
        self.status_var.set(title)
        if notify:
            messagebox.showerror(title, detail)
        if self.closing:
            self.destroy()

    def refresh_choice_catalogs(self) -> None:
        """从当前数据生成只读选择项，兼容已有合法值。"""
        terrain_values: set[str] = set()
        tag_values: set[str] = set()
        region_values: set[str] = set()
        specific_actions: set[str] = set()
        for card in self.cards:
            elements = card.get("地图", {}).get("地图元素", {})
            terrain = str(elements.get("地形主类型") or "").strip()
            region = str(elements.get("地区标记") or "").strip()
            if terrain:
                terrain_values.add(terrain)
            if region:
                region_values.add(region)
            for tag in elements.get("地形标签") or []:
                if str(tag).strip():
                    tag_values.add(str(tag).strip())
            for slot in card.get("挑战骰", {}).get("槽位", []):
                terrain_zh = str(slot.get("挑战骰地形", {}).get("中文") or "").strip()
                action = str(slot.get("特定行动") or "").strip()
                if terrain_zh and terrain_zh != "未指定/未识别":
                    terrain_values.add(terrain_zh)
                if action:
                    specific_actions.add(action)
        self.map_terrain_combo.configure(values=[""] + sorted(terrain_values))
        self.map_region_combo.configure(values=[""] + sorted(region_values))
        self.slot_terrain_combo.configure(values=["未指定/未识别"] + sorted(terrain_values))
        self.slot_action_combo.configure(values=[""] + sorted(specific_actions))
        self.map_tags_list.delete(0, tk.END)
        for value in sorted(tag_values or terrain_values):
            self.map_tags_list.insert(tk.END, value)

    def select_listbox_values(self, widget: tk.Listbox, values: list[str]) -> None:
        wanted = {str(value) for value in values}
        widget.selection_clear(0, tk.END)
        for index in range(widget.size()):
            if widget.get(index) in wanted:
                widget.selection_set(index)

    def selected_listbox_values(self, widget: tk.Listbox) -> list[str]:
        return [str(widget.get(index)) for index in widget.curselection()]

    def populate_list(self, numbers: list[str] | None = None) -> None:
        self.card_list.delete(0, tk.END)
        source_numbers = numbers if numbers is not None else [str(card.get("编号")) for card in self.cards]
        for number in source_numbers:
            card = self.by_number[number]
            self.card_list.insert(tk.END, self.card_list_text(card))
        unresolved_cards = sum(self.has_unresolved_slot(card) for card in self.cards)
        unverified_cards = sum(card.get("人工校对", {}).get("总状态", "未校对") != "已核验" for card in self.cards)
        self.queue_count_var.set(f"当前显示 {len(source_numbers)} / {len(self.cards)}\n待人工核验 {unverified_cards}｜骰槽待查 {unresolved_cards}")

    def has_unresolved_slot(self, card: dict[str, Any]) -> bool:
        return any(bool(slot.get("未解决问题")) for slot in card.get("挑战骰", {}).get("槽位", []))

    def has_manual_revision(self, card: dict[str, Any]) -> bool:
        review = card.get("人工校对", {})
        if review.get("修订状态") == "已修订":
            return True
        fields = (
            "名字修订", "基础文本修订", "地图事件修订", "地图元素修订", "罗盘道路修订",
            "地点行动修订", "图画内地点行动修订", "挑战骰修订", "小卡行动修订", "强化修订",
            "技能效果修订", "技能花销修订", "小卡类型修订", "储备容量修订", "价值修订",
            "放置时强化点数修订", "强化容量修订", "问题列表", "备注",
        )
        return any(review.get(field) not in (None, "", [], {}) for field in fields)

    def card_list_text(self, card: dict[str, Any]) -> str:
        number = str(card.get("编号"))
        kind = card_kind(card)
        state = self.revision_display_state(card)
        marker = f"【{state}】" if state else ""
        review = card.get("人工校对", {})
        if card.get("牌面总结", {}).get("人工核验优先级") == "优先":
            marker += "【AI建议优先】"
        if self.has_unresolved_slot(card):
            marker += "【骰槽待查】"
        if str(review.get("待AI处理提示词") or "").strip():
            marker += "【AI已处理】" if review.get("AI处理状态") == "已完成" else "【待AI】"
        if kind == "大卡":
            name = "大卡"
        else:
            name = card.get("牌面总结", {}).get("标题") or card.get("名字", {}).get("人工修订值") or card.get("名字", {}).get("当前中文名") or "未命名"
        return f"{number} {marker} {str(name)[:15]}"

    def refresh_card_list_entry(self, number: str) -> None:
        """立即重绘当前卡牌状态，不重置列表选择。"""
        if not hasattr(self, "card_list"):
            return
        for index in range(self.card_list.size()):
            if self.card_list.get(index).split()[0] == number:
                selected = index in self.card_list.curselection()
                self.card_list.delete(index)
                self.card_list.insert(index, self.card_list_text(self.by_number[number]))
                if selected:
                    self.card_list.selection_set(index)
                return

    def is_card_revised(self, card: dict[str, Any]) -> bool:
        return card.get("人工校对", {}).get("修订状态") == "已修订"

    def revision_display_state(self, card: dict[str, Any]) -> str:
        number = str(card.get("编号"))
        if number in self.pending_revision_numbers:
            return "已修订未保存"
        return "已修订" if self.is_card_revised(card) else ""

    def mark_card_revised(self, card: dict[str, Any]) -> None:
        review = card.setdefault("人工校对", {})
        review["修订状态"] = "已修订"
        review["最后实际修订时间"] = datetime.now().astimezone().isoformat(timespec="seconds")
        number = str(card.get("编号"))
        self.pending_revision_numbers.add(number)
        self.refresh_card_list_entry(number)
        self.status_var.set(f"{number} 已修订未保存")

    def mark_current_unrevised(self) -> None:
        if not self.current_number:
            return
        card = self.by_number[self.current_number]
        self.push_undo(self.current_number, card)
        review = card.setdefault("人工校对", {})
        review["修订状态"] = "未修订"
        review.pop("最后实际修订时间", None)
        self.pending_revision_numbers.discard(str(card.get("编号")))
        self.user_modified_current = False
        self.dirty = True
        self.refresh_card_list_entry(str(card.get("编号")))
        self.filter_cards()
        self.status_var.set(f"卡牌 {self.current_number} 已标记为未修订")

    def filter_cards(self) -> None:
        query = self.search_var.get().strip().lower()
        wanted_type = self.type_filter.get()
        wanted_review = self.review_filter.get()
        result = []
        for card in self.cards:
            number = str(card.get("编号"))
            is_location = card.get("地图", {}).get("是否地点牌", False)
            name = "" if is_location else (card.get("名字", {}).get("人工修订值") or card.get("名字", {}).get("当前中文名") or "")
            desc = card.get("基础文本描述", {}).get("人工修订值") or card.get("基础文本描述", {}).get("当前中文描述") or ""
            display_kind = card_kind(card)
            review_data = card.get("人工校对", {})
            review = review_data.get("总状态", "未校对")
            ai_prompt = str(review_data.get("待AI处理提示词") or "").strip()
            summary = str(card.get("牌面总结", {}).get("内容") or "")
            summary_priority = str(card.get("牌面总结", {}).get("人工核验优先级") or "")
            ai_status = str(review_data.get("AI处理状态") or "")
            if query and query not in (number + name + desc + summary + ai_prompt).lower():
                continue
            if wanted_type != "全部" and display_kind != wanted_type:
                continue
            display_state = self.revision_display_state(card)
            if wanted_review == "待人工核验" and review == "已核验":
                continue
            if wanted_review == "有未解决骰槽" and not self.has_unresolved_slot(card):
                continue
            if wanted_review == "有人工修订" and not self.has_manual_revision(card):
                continue
            if wanted_review == "AI建议优先" and summary_priority != "优先":
                continue
            if wanted_review == "待AI" and (not ai_prompt or ai_status == "已完成"):
                continue
            if wanted_review == "AI已处理" and ai_status != "已完成":
                continue
            if wanted_review == "有提示词" and not ai_prompt:
                continue
            if wanted_review == "已修订未保存" and display_state != "已修订未保存":
                continue
            if wanted_review == "已修订" and display_state != "已修订":
                continue
            if wanted_review == "未修订" and display_state:
                continue
            if wanted_review not in ("全部", "待人工核验", "有未解决骰槽", "有人工修订", "AI建议优先", "待AI", "AI已处理", "有提示词", "已修订未保存", "已修订", "未修订") and review != wanted_review:
                continue
            result.append(number)
        self.populate_list(result)

    def on_card_select(self, _event: Any = None) -> None:
        selected = self.card_list.curselection()
        if not selected:
            return
        number = self.card_list.get(selected[0]).split()[0]
        if self.current_number and self.current_number != number:
            self.commit_current()
        self.current_number = number
        self.load_card(self.by_number[number])

    def load_card(self, card: dict[str, Any]) -> None:
        self.loading = True
        number = str(card.get("编号"))
        is_location = card.get("地图", {}).get("是否地点牌", False)
        display_kind = card_kind(card)
        small_name = card.get("名字", {}).get("人工修订值") or card.get("名字", {}).get("当前中文名") or "未命名"
        summary_title = card.get("牌面总结", {}).get("标题") or small_name
        image_heading = f"{display_kind} {number}" if display_kind == "大卡" else f"{display_kind} {number}｜{summary_title}"
        self.image_title.configure(text=image_heading)
        self.base_number.set(number)
        self.base_type.set(card.get("基础信息", {}).get("卡牌类型", {}).get("中文", ""))
        self.base_subtype.set(card.get("基础信息", {}).get("小卡类型", {}).get("中文", ""))
        if is_location:
            self.base_detected_name.set("地点牌不使用名称")
            self.base_name.set("")
            self.base_name_entry.configure(state="disabled")
        else:
            self.base_detected_name.set(card.get("名字", {}).get("当前中文名", ""))
            self.base_name.set(card.get("名字", {}).get("人工修订值", ""))
            self.base_name_entry.configure(state="normal")
        self.base_value.set("" if card.get("基础信息", {}).get("价值", {}).get("当前值") is None else str(card["基础信息"]["价值"]["当前值"]))
        self.base_texture.set(card.get("基础信息", {}).get("中文源图片", ""))
        self.base_detected_text.configure(state="normal")
        text_set(self.base_detected_text, card.get("基础文本描述", {}).get("当前中文描述", ""))
        self.base_detected_text.configure(state="disabled")
        text_set(self.base_text, card.get("基础文本描述", {}).get("人工修订值", ""))
        elements = card.get("地图", {}).get("地图元素", {})
        self.map_terrain.set(elements.get("地形主类型") or "")
        self.select_listbox_values(self.map_tags_list, list(elements.get("地形标签") or []))
        self.map_region.set(elements.get("地区标记") or "")
        marker = elements.get("元素或区域标记")
        self.map_marker.set(marker if isinstance(marker, str) else json.dumps(marker, ensure_ascii=False) if marker else "")
        roads = card.get("地图", {}).get("上下左右道路罗盘", {})
        for key, _label in DIR_KEYS:
            road = roads.get(key, {})
            vars_ = self.road_vars[key]
            vars_["state"].set(road.get("道路状态") or "")
            vars_["printed"].set("" if road.get("罗盘印刷值") is None else str(road.get("罗盘印刷值")))
            vars_["destination"].set("" if road.get("目标地点编号") is None else str(road.get("目标地点编号")))
            vars_["cost"].set("" if road.get("移动花费") is None else str(road.get("移动花费")))
        self.current_action_ref = None
        self.current_slot_index = None
        self.current_boost_index = None
        self.populate_actions(card)
        self.populate_slots(card)
        small = card.get("小卡", {})
        self.small_type.set(small.get("小卡类型", {}).get("中文", "") or "未分类/不适用")
        reserve_field = small.get("文字规则中的储备容量", small.get("储备容量", {}))
        self.small_reserve.set("" if reserve_field.get("当前值") is None else str(reserve_field["当前值"]))
        price_field = small.get("价格", small.get("价值", {}))
        self.small_value.set("" if price_field.get("当前值") is None else str(price_field["当前值"]))
        self.small_initial.set("" if small.get("放置时强化点数", {}).get("当前值") is None else str(small["放置时强化点数"]["当前值"]))
        capacity_field = small.get("强化储备数（灰色方格）", small.get("强化容量", {}))
        self.small_capacity.set(str(capacity_field.get("当前值", 0)))
        self.populate_boost_slots(card)
        text_set(self.small_placement, self.readable_lines(small.get("放置效果", [])))
        effects = small.get("技能效果", {})
        skill_lines = list(effects.get("中文卡面冒号文本候选", []))
        for ability in effects.get("结构化卡牌能力", []):
            skill_lines.append(self.human_ability_line(ability))
        text_set(self.small_skills, "\n".join(skill_lines))
        costs = small.get("技能花销", {})
        cost_lines = [f"能力花费：{self.human_cost(item)}" for item in costs.get("卡牌能力花销", [])]
        cost_lines += [f"{item.get('行动文字')}｜条目{item.get('故事条目')}｜花费{item.get('花费')}" for item in costs.get("行动故事花费", [])]
        text_set(self.small_costs, "\n".join(cost_lines))
        review = card.get("人工校对", {})
        self.review_status.set(review.get("总状态", "未校对"))
        self.review_summary.configure(state="normal")
        text_set(self.review_summary, card.get("牌面总结", {}).get("内容", ""))
        self.review_summary.configure(state="disabled")
        text_set(self.review_issues, "\n".join(review.get("问题列表", [])))
        text_set(self.review_notes, review.get("备注", ""))
        text_set(self.review_ai_prompt, review.get("待AI处理提示词", ""))
        ai_status = str(review.get("AI处理状态") or "")
        self.review_ai_status.set(
            f"AI处理状态：{ai_status}｜{review.get('AI处理结果摘要', '')}" if ai_status else "未填写提示词"
        )
        unresolved_slots = sum(bool(slot.get("未解决问题")) for slot in card.get("挑战骰", {}).get("槽位", []))
        if review.get("总状态") == "已核验":
            checklist_status = "状态：已核验。若结构化数据后来发生变化，请再次对照卡图。"
        elif unresolved_slots:
            checklist_status = f"状态：待人工核验；其中 {unresolved_slots} 个骰槽有机器未解决项，请优先检查。"
        else:
            checklist_status = "状态：待人工核验。按下方顺序逐项看卡图即可。"
        self.checklist_status_var.set(checklist_status)
        self.checklist_text.configure(state="normal")
        text_set(self.checklist_text, self.human_checklist_text(card))
        self.checklist_text.configure(state="disabled")
        self.refresh_advanced()
        self.image_zoom = 1.0
        default_rotation = DEFAULT_IMAGE_ROTATIONS.get(str(card.get("编号") or "").zfill(4), 0)
        self.image_rotation = int(card.get("人工校对", {}).get("图片显示旋转度数", default_rotation) or 0) % 360
        self.refresh_image()
        self.apply_display_mode()
        self.loading = False
        self.user_modified_current = False
        self.status_var.set(f"{number}｜骰槽 {card.get('挑战骰', {}).get('槽位总数', 0)}｜{self.review_status.get()}")

    def human_checklist_text(self, card: dict[str, Any]) -> str:
        summary = str(card.get("牌面总结", {}).get("内容") or "").strip()
        visible_summary = summary.split("\n人工核验：", 1)[0].strip()
        instructions = [
            "【建议核对顺序】",
            "1. 卡号、名称、主类型和卡面上的每个子标签。",
            "2. 价格（彩色数字标记）、强化储备数（灰色方格）、放置强化，以及所有卡面文字。",
            "3. 每个完整留白方框才算骰槽；核对框位、颜色/结果、花费、生成和闪电标志。",
            "4. 主动技能、被动技能、圆形效果和彩色行动条不是骰槽，但它们的文字效果仍必须保留。",
            "5. 行动、选项、条件、页码、失去/替换卡牌等句子都属于有效规则，不能省略。",
            "",
            "【当前结构化内容】",
            visible_summary or "当前没有生成可读总结，请在“总结与AI纠正”页记录问题。",
        ]
        markers = card.get("卡面标记槽", {})
        marker_count = int(markers.get("数量", 0) or 0)
        if marker_count:
            instructions.extend([
                "",
                "【独立卡面标记槽】",
                f"- {markers.get('类型') or '任务标记'}：{marker_count}格。紫色框显示；它们不是挑战骰槽或强化槽。",
            ])
        printed_skills = card.get("卡面技能标记", {}).get("标记", [])
        if printed_skills:
            labels = "、".join(
                f"{item.get('label_zh')}（{item.get('color_zh')}）"
                for item in printed_skills if isinstance(item, dict)
            )
            instructions.extend([
                "",
                "【卡面技能图示】",
                f"- {labels}。这些图示必须保留，但不是骰槽，也不直接作为地点故事行动。",
            ])
        unresolved = [slot for slot in card.get("挑战骰", {}).get("槽位", []) if slot.get("未解决问题")]
        if unresolved:
            instructions.extend([
                "",
                "【需要优先看的地方】",
                f"- 系统仍不确定 {len(unresolved)} 个骰槽。请直接看左侧卡图，确认是否真有完整方框以及框是否贴合边界。",
            ])
        prompt = str(card.get("人工校对", {}).get("待AI处理提示词") or "").strip()
        if prompt:
            instructions.extend(["", "【已记录的纠正】", prompt])
        slot_revisions = card.get("人工校对", {}).get("挑战骰修订", [])
        if slot_revisions:
            instructions.extend([
                "",
                "【骰槽人工最终结论】",
                "下列删除表示误识别骰槽必须保持删除；位置调整表示人工确认后的最终框位。",
            ])
            for item in slot_revisions:
                if isinstance(item, dict):
                    operation = str(item.get("操作") or "修订")
                    detail = str(item.get("说明") or "").strip()
                    instructions.append(f"- {operation}：{detail}" if detail else f"- {operation}")
        return "\n".join(instructions)

    def readable_lines(self, values: Any) -> str:
        if not values:
            return ""
        lines = []
        for value in values if isinstance(values, list) else [values]:
            if isinstance(value, dict):
                direct_text = str(value.get("text_zh") or value.get("中文") or value.get("text") or "").strip()
                if direct_text:
                    lines.append(direct_text)
                    continue
                command = str(value.get("command") or value.get("命令") or "效果")
                command_names = {
                    "choose": "选择一项",
                    "adjust_stat": "调整属性",
                    "gain_skill": "获得技能",
                    "lose_skill": "失去技能",
                    "adjust_card_reinforcement": "调整卡牌强化",
                    "set_card_reinforcement": "设置卡牌强化",
                    "gain_card": "获得卡牌",
                    "lose_card": "失去卡牌",
                    "replace_card": "替换卡牌",
                    "reroll_challenge_dice": "重掷挑战骰",
                    "continue_action": "继续/取得另一个行动结果",
                    "inspect_world_book_page": "查看《万境之书》页码",
                    "add_hazard_damage_shield": "抵消环境伤害",
                }
                field_names = {
                    "amount": "数量", "stat": "属性", "skill": "技能", "card_id": "卡牌",
                    "page": "页码", "hazard_type": "伤害类型", "scope": "范围", "cost_reduction": "费用减少",
                }
                useful = []
                for key, item in value.items():
                    if key in ("command", "命令", "source", "source_uid", "event_uid", "action_uid", "confidence"):
                        continue
                    if key == "options" and isinstance(item, list):
                        option_labels = [str(option.get("label") or option.get("text_zh") or option.get("text") or f"选项{index + 1}") for index, option in enumerate(item) if isinstance(option, dict)]
                        if option_labels:
                            useful.append("；".join(option_labels))
                        continue
                    if isinstance(item, (dict, list)):
                        continue
                    useful.append(f"{field_names.get(key, key)}：{item}")
                label = command_names.get(command, "规则效果")
                lines.append(f"{label}：{'，'.join(useful)}" if useful else label)
            else:
                lines.append(str(value))
        return "\n".join(lines)

    def human_cost(self, value: Any) -> str:
        if not isinstance(value, dict):
            return str(value)
        labels = {
            "card_reinforcement": "本卡强化",
            "challenge_dice": "挑战骰",
            "boost": "公共强化",
            "time": "时间",
            "health": "生命",
            "morale": "士气",
            "coins": "金钱",
        }
        parts = [f"{labels.get(str(key), str(key))}{amount}" for key, amount in value.items() if amount not in (None, 0, "")]
        return "、".join(parts) if parts else "无"

    def human_ability_line(self, ability: dict[str, Any]) -> str:
        label = str(ability.get("label_zh") or "能力").strip()
        text = str(ability.get("text_zh") or "").strip()
        activation = {
            "manual": "主动技能", "action": "卡面行动", "passive": "被动技能",
            "triggered": "触发技能", "reactive": "反应技能", "skirm_only": "交锋技能",
        }.get(str(ability.get("activation") or ""), "技能")
        heading = f"{label}（{activation}；花费：{self.human_cost(ability.get('cost', {}))}）"
        if not text or text == label:
            return heading
        return f"{heading}：{text}"

    def make_manual_action(self, card: dict[str, Any], section: str, index: int) -> dict[str, Any]:
        number = str(card.get("编号"))
        uid = f"action:{number}:manual:{section}:{index + 1:02d}"
        event_uid = f"story:manual:{number}:{section}:{index + 1:02d}"
        return {
            "行动UID": uid,
            "行动来源类型": {"原值": "manual", "中文": "人工新增行动"},
            "行动家族": "自定义",
            "行动文字": "新行动",
            "故事书": {"原值": "", "中文": ""},
            "故事条目": None,
            "执行状态": "未执行",
            "使用规则": {"原值": "once_per_player_per_location", "是否可重复": False, "依据": "default_location_action_limit"},
            "对应地图事件": {
                "事件UID": event_uid,
                "标题": "",
                "中文正文": "",
                "花费": None,
                "结构化效果": [],
                "结构化选项": [],
                "事件来源": "manual_visual_editor",
            },
        }

    def make_manual_arrival_event(self, card: dict[str, Any], index: int) -> dict[str, Any]:
        number = str(card.get("编号"))
        return {
            "事件UID": f"arrival:{number}:manual:{index + 1:02d}",
            "text": "新抵达事件",
            "execution_status": "未执行",
            "commands": [],
            "事件来源": "manual_visual_editor",
        }

    def action_lists(self, card: dict[str, Any], section: str) -> list[dict[str, Any]]:
        if section == "arrival":
            events = card.setdefault("地图", {}).setdefault("地图事件", {})
            return events.setdefault("抵达强制事件", [])
        return card.setdefault("地图", {}).setdefault(section, [])

    def select_action_row(self, section: str, index: int) -> None:
        iid = f"{section}:{index}"
        self.populate_actions(self.by_number[self.current_number])
        if self.action_tree.exists(iid):
            self.action_tree.selection_set(iid)
            self.action_tree.focus(iid)
            self.action_tree.see(iid)
            self.on_action_select()

    def add_action(self, section: str) -> None:
        if not self.current_number:
            return
        card = self.by_number[self.current_number]
        actions = self.action_lists(card, section)
        self.push_undo(self.current_number, card)
        actions.append(self.make_manual_action(card, section, len(actions)))
        self.mark_card_revised(card)
        self.dirty = True
        self.current_action_ref = None
        self.select_action_row(section, len(actions) - 1)
        self.status_var.set(f"已新增{('地点行动' if section == '地点行动' else '图画内行动')}，尚未写入磁盘")

    def add_arrival_event(self) -> None:
        if not self.current_number:
            return
        card = self.by_number[self.current_number]
        events = self.action_lists(card, "arrival")
        self.push_undo(self.current_number, card)
        events.append(self.make_manual_arrival_event(card, len(events)))
        self.mark_card_revised(card)
        self.dirty = True
        self.current_action_ref = None
        self.select_action_row("arrival", len(events) - 1)
        self.status_var.set("已新增抵达事件，尚未写入磁盘")

    def duplicate_action(self) -> None:
        if not self.current_number or not self.current_action_ref:
            messagebox.showinfo("复制事件/行动", "请先选择一条事件或行动。")
            return
        section, index = self.current_action_ref
        card = self.by_number[self.current_number]
        items = self.action_lists(card, section)
        if index >= len(items):
            return
        self.push_undo(self.current_number, card)
        copied = deepcopy(items[index])
        copied["事件来源"] = "manual_visual_editor"
        items.insert(index + 1, copied)
        self.mark_card_revised(card)
        self.dirty = True
        self.current_action_ref = None
        self.select_action_row(section, index + 1)
        self.status_var.set("已复制事件/行动，尚未写入磁盘")

    def delete_action(self) -> None:
        if not self.current_number or not self.current_action_ref:
            messagebox.showinfo("删除事件/行动", "请先选择一条事件或行动。")
            return
        section, index = self.current_action_ref
        card = self.by_number[self.current_number]
        items = self.action_lists(card, section)
        if index >= len(items):
            return
        if not messagebox.askyesno("删除事件/行动", "确定删除当前选中的事件/行动吗？", parent=self):
            return
        self.push_undo(self.current_number, card)
        items.pop(index)
        self.mark_card_revised(card)
        self.dirty = True
        self.current_action_ref = None
        self.populate_actions(card)
        self.refresh_advanced()
        self.status_var.set("已删除事件/行动，尚未写入磁盘")

    def populate_actions(self, card: dict[str, Any]) -> None:
        self.action_tree.delete(*self.action_tree.get_children())
        for i, effect in enumerate(card.get("地图", {}).get("地图事件", {}).get("抵达强制事件", [])):
            self.action_tree.insert("", "end", iid=f"arrival:{i}", values=("抵达事件", effect.get("text", ""), "", "", "", "", effect.get("execution_status", "")))
        for section, label in (("地点行动", "地点行动"), ("图画内地点行动", "图画行动")):
            for i, action in enumerate(card.get("地图", {}).get(section, [])):
                event = action.get("对应地图事件") or {}
                rule = action.get("使用规则") or {}
                repeatable = bool(rule.get("是否可重复", rule.get("原值") in {"repeatable", "unrestricted"}))
                self.action_tree.insert("", "end", iid=f"{section}:{i}", values=(label, action.get("行动文字", ""), action.get("故事书", {}).get("中文", ""), action.get("故事条目", ""), event.get("花费", ""), "是" if repeatable else "否", action.get("执行状态", "")))
        self.clear_action_form()

    def clear_action_form(self) -> None:
        for var in (self.action_kind, self.action_text, self.action_family, self.action_book, self.action_entry, self.action_cost, self.action_title, self.action_status, self.action_usage_evidence):
            var.set("")
        self.action_repeatable.set(False)
        self.action_repeatable_check.configure(state="disabled")
        text_set(self.action_event_text, "")
        self.action_structure.configure(state="normal")
        text_set(self.action_structure, "")
        self.action_structure.configure(state="disabled")

    def on_action_select(self, _event: Any = None) -> None:
        if self.current_action_ref:
            self.apply_action(refresh=False)
        selection = self.action_tree.selection()
        if not selection or not self.current_number:
            return
        section, index_text = selection[0].split(":", 1)
        index = int(index_text)
        self.current_action_ref = (section, index)
        card = self.by_number[self.current_number]
        if section == "arrival":
            effect = card["地图"]["地图事件"]["抵达强制事件"][index]
            self.action_kind.set("抵达强制事件")
            self.action_text.set(effect.get("text", ""))
            self.action_family.set("")
            self.action_book.set("")
            self.action_entry.set("")
            self.action_cost.set("")
            self.action_title.set("")
            self.action_status.set(effect.get("execution_status", ""))
            self.action_repeatable.set(False)
            self.action_usage_evidence.set("")
            self.action_repeatable_check.configure(state="disabled")
            text_set(self.action_event_text, effect.get("text", ""))
            structure = self.readable_lines(effect.get("commands", []))
        else:
            action = card["地图"][section][index]
            event = action.get("对应地图事件") or {}
            rule = action.get("使用规则") or {}
            self.action_kind.set("图画内行动" if section == "图画内地点行动" else "地点行动")
            self.action_text.set(action.get("行动文字", ""))
            self.action_family.set(action.get("行动家族", ""))
            self.action_book.set(action.get("故事书", {}).get("中文", ""))
            self.action_entry.set("" if action.get("故事条目") is None else str(action.get("故事条目")))
            self.action_cost.set("" if event.get("花费") is None else str(event.get("花费")))
            self.action_title.set(event.get("标题", "") or "")
            self.action_status.set(action.get("执行状态", ""))
            self.action_repeatable.set(bool(rule.get("是否可重复", rule.get("原值") in {"repeatable", "unrestricted"})))
            self.action_usage_evidence.set(str(rule.get("依据") or ""))
            self.action_repeatable_check.configure(state="normal")
            text_set(self.action_event_text, event.get("中文正文", "") or "")
            structure = "【效果】\n" + self.readable_lines(event.get("结构化效果", [])) + "\n\n【选项】\n" + self.readable_lines(event.get("结构化选项", []))
        self.action_structure.configure(state="normal")
        text_set(self.action_structure, structure)
        self.action_structure.configure(state="disabled")

    def apply_action(self, refresh: bool = True) -> None:
        if not self.current_action_ref or not self.current_number:
            return
        card = self.by_number[self.current_number]
        section, index = self.current_action_ref
        before_card = deepcopy(card)
        before = json.dumps(card["地图"], ensure_ascii=False, sort_keys=True)
        if section == "arrival":
            effect = card["地图"]["地图事件"]["抵达强制事件"][index]
            effect["text"] = text_get(self.action_event_text) or self.action_text.get()
            effect["execution_status"] = self.action_status.get()
        else:
            action = card["地图"][section][index]
            action["行动文字"] = self.action_text.get()
            action["行动家族"] = self.action_family.get()
            book_zh = self.action_book.get()
            action.setdefault("故事书", {})["中文"] = book_zh
            action["故事书"]["原值"] = STORY_BOOK_CODES.get(book_zh, "")
            action["故事条目"] = parse_int(self.action_entry.get())
            action["执行状态"] = self.action_status.get()
            repeatable = bool(self.action_repeatable.get())
            rule = action.setdefault("使用规则", {})
            rule["原值"] = "repeatable" if repeatable else "once_per_player_per_location"
            rule["是否可重复"] = repeatable
            rule["依据"] = self.action_usage_evidence.get().strip()
            event = action.get("对应地图事件")
            if event is not None:
                event["标题"] = self.action_title.get()
                event["中文正文"] = text_get(self.action_event_text)
                event["花费"] = parse_cost(self.action_cost.get())
        changed = before != json.dumps(card["地图"], ensure_ascii=False, sort_keys=True)
        if changed:
            self.push_undo(self.current_number, before_card)
            if refresh or self.user_modified_current:
                self.mark_card_revised(card)
        self.dirty = self.dirty or changed
        if refresh:
            self.populate_actions(card)
            self.current_action_ref = None
            self.status_var.set("已应用事件/行动修改，尚未写入磁盘")

    def populate_slots(self, card: dict[str, Any]) -> None:
        self.slot_tree.delete(*self.slot_tree.get_children())
        for i, slot in enumerate(card.get("挑战骰", {}).get("槽位", [])):
            self.slot_tree.insert("", "end", iid=str(i), values=(slot.get("槽位序号"), slot.get("槽位类型", {}).get("中文"), slot.get("技能类型", {}).get("中文"), slot.get("挑战骰结果要求", {}).get("中文"), "、".join(item.get("中文", "") for item in slot.get("挑战骰能力要求", [])), slot.get("挑战骰强化花费"), slot.get("挑战骰强化生成"), "是" if slot.get("挑战骰闪电标志") else "否", slot.get("总体识别置信度")))

    def default_bbox(self, size_ratio: float) -> list[float]:
        if self.image_source is not None:
            width, height = self.image_source.size
        else:
            width, height = 1370, 2055
        size = max(60.0, min(width, height) * size_ratio)
        return [round((width - size) / 2, 2), round((height - size) / 2, 2), round((width + size) / 2, 2), round((height + size) / 2, 2)]

    def fixed_challenge_bbox(self, bbox: list[float] | None = None) -> list[float]:
        """保留已有框的左上角，使用卡牌0803的334×327标准骰槽尺寸。"""
        if self.image_source is not None:
            image_width, image_height = self.image_source.size
        else:
            image_width, image_height = 1370, 2055
        if bbox and len(bbox) == 4:
            anchor_x = float(bbox[0])
            anchor_y = float(bbox[1])
        else:
            anchor_x = (image_width - CHALLENGE_SLOT_WIDTH) / 2.0
            anchor_y = (image_height - CHALLENGE_SLOT_HEIGHT) / 2.0
        slot_width = min(CHALLENGE_SLOT_WIDTH, float(image_width))
        slot_height = min(CHALLENGE_SLOT_HEIGHT, float(image_height))
        x1 = max(0.0, min(anchor_x, image_width - slot_width))
        y1 = max(0.0, min(anchor_y, image_height - slot_height))
        x1 = round(x1, 2)
        y1 = round(y1, 2)
        return [x1, y1, round(x1 + slot_width, 2), round(y1 + slot_height, 2)]

    def make_manual_slot(self, card: dict[str, Any], bbox: list[float]) -> dict[str, Any]:
        number = str(card.get("编号"))
        return {
            "槽位UID": f"slot:{number}:manual",
            "槽位序号": 0,
            "槽位类型": {"原值": "manual", "中文": "未分类骰槽"},
            "技能类型": {"原值": "", "中文": "未识别", "卡面文字": ""},
            "技能颜色": {"原值": "", "中文": "无/未识别"},
            "技能家族": "",
            "挑战骰结果要求": {"原值": "", "中文": "任意结果/未指定"},
            "挑战骰能力要求": [],
            "能力文字": "",
            "能力颜色OCR": "",
            "挑战骰互动元素": {"是否互动通配槽": False, "互动图标圆": None},
            "挑战骰地形": {"原值": "", "中文": "未指定/未识别"},
            "特定行动": "",
            "额外投骰数量": 0,
            "挑战骰强化花费": 0,
            "挑战骰强化生成": 0,
            "强化流向": {"原值": "", "中文": "无强化流向"},
            "挑战骰闪电标志": False,
            "坐标_原图像素": bbox,
            "坐标_归一化": [],
            "技能图标圆": None,
            "强化箭头坐标": None,
            "强化方块坐标": [],
            "闪电图标坐标": None,
            "总体识别置信度": 1.0,
            "技能符号来源": "manual_visual_editor",
            "技能符号置信度": 1.0,
            "技能符号校对状态": "human_created",
            "能力符号来源": [],
            "能力符号校对状态": "human_created",
            "未解决问题": [],
            "原始槽位数据": {
                "slot_uid": f"slot:{number}:manual", "card_uid": f"card:{number}",
                "card_number": number, "slot_index": 0, "slot_type": "manual",
                "bbox": bbox, "bbox_normalized": [], "source": "manual_visual_editor",
            },
        }

    def reindex_challenge_slots(self, card: dict[str, Any]) -> None:
        number = str(card.get("编号"))
        slots = card.setdefault("挑战骰", {}).setdefault("槽位", [])
        type_counts = {key: 0 for key in ("技能类型槽数量", "能力要求槽数量", "指定结果槽数量", "互动元素槽数量", "特定行动额外投骰槽数量")}
        type_map = {
            "技能类型骰槽": "技能类型槽数量", "能力要求骰槽": "能力要求槽数量",
            "指定结果骰槽": "指定结果槽数量", "互动元素通配骰槽": "互动元素槽数量",
            "特定行动额外投骰": "特定行动额外投骰槽数量",
        }
        for index, slot in enumerate(slots, 1):
            uid = f"slot:{number}:{index:02d}"
            slot["槽位UID"] = uid
            slot["槽位序号"] = index
            raw = slot.setdefault("原始槽位数据", {})
            raw.update({"slot_uid": uid, "card_uid": f"card:{number}", "card_number": number, "slot_index": index})
            key = type_map.get(slot.get("槽位类型", {}).get("中文"))
            if key:
                type_counts[key] += 1
        challenge = card["挑战骰"]
        challenge["槽位总数"] = len(slots)
        challenge.update(type_counts)
        challenge["闪电标志槽数量"] = sum(bool(slot.get("挑战骰闪电标志")) for slot in slots)
        challenge["强化总花费"] = sum(int(slot.get("挑战骰强化花费", 0) or 0) for slot in slots)
        challenge["强化总生成"] = sum(int(slot.get("挑战骰强化生成", 0) or 0) for slot in slots)
        challenge["未分类槽数量"] = sum(slot.get("槽位类型", {}).get("中文") == "未分类骰槽" for slot in slots)
        small = card.setdefault("小卡", {})
        small.setdefault("挑战骰", {})["槽位总数"] = len(slots)
        small["挑战骰"]["槽位"] = deepcopy(slots) if small.get("是否小卡") else []
        raw_card = card.setdefault("原始结构化卡牌数据", {})
        raw_card["challenge_slots"] = [deepcopy(slot.get("原始槽位数据", {})) for slot in slots]
        components = raw_card.setdefault("components", {})
        components["challenge_slot_count"] = len(slots)
        components["skill_type_slot_count"] = type_counts["技能类型槽数量"]
        components["ability_slot_count"] = type_counts["能力要求槽数量"]
        components["specific_result_slot_count"] = type_counts["指定结果槽数量"]
        components["interaction_slot_count"] = type_counts["互动元素槽数量"]
        components["action_specific_dice_modifier_slot_count"] = type_counts["特定行动额外投骰槽数量"]
        components["impact_slot_count"] = challenge["闪电标志槽数量"]
        components["total_slot_boost_cost"] = challenge["强化总花费"]
        components["total_slot_boost_reward"] = challenge["强化总生成"]
        components["unclassified_slot_count"] = challenge["未分类槽数量"]
        components["slot_uids"] = [slot["槽位UID"] for slot in slots]

    def log_slot_change(self, card: dict[str, Any], operation: str, detail: str) -> None:
        self.mark_card_revised(card)
        review = card.setdefault("人工校对", {})
        changes = review.setdefault("挑战骰修订", [])
        changes.append({"操作": operation, "说明": detail, "时间": datetime.now().astimezone().isoformat(timespec="seconds")})

    def add_challenge_slot(self) -> None:
        if not self.current_number:
            return
        card = self.by_number[self.current_number]
        self.push_undo(self.current_number, card)
        slot = self.make_manual_slot(card, self.fixed_challenge_bbox())
        card.setdefault("挑战骰", {}).setdefault("槽位", []).append(slot)
        self.set_slot_bbox(card, len(card["挑战骰"]["槽位"]) - 1, slot["坐标_原图像素"])
        self.reindex_challenge_slots(card)
        self.log_slot_change(card, "新增", f"新增槽位 {len(card['挑战骰']['槽位'])}")
        self.dirty = True
        self.populate_slots(card)
        target = len(card["挑战骰"]["槽位"]) - 1
        self.current_slot_index = None
        self.slot_tree.selection_set(str(target))
        self.on_slot_select()
        self.refresh_image()

    def duplicate_challenge_slot(self) -> None:
        if not self.current_number or self.current_slot_index is None:
            messagebox.showinfo("复制骰槽", "请先选择一个挑战骰槽。")
            return
        card = self.by_number[self.current_number]
        self.push_undo(self.current_number, card)
        original = card["挑战骰"]["槽位"][self.current_slot_index]
        copied = deepcopy(original)
        bbox = list(copied.get("坐标_原图像素", self.fixed_challenge_bbox()))
        copied["坐标_原图像素"] = [value + 35 for value in bbox]
        card["挑战骰"]["槽位"].insert(self.current_slot_index + 1, copied)
        self.set_slot_bbox(card, self.current_slot_index + 1, copied["坐标_原图像素"])
        self.reindex_challenge_slots(card)
        self.log_slot_change(card, "复制", f"复制原槽位 {self.current_slot_index + 1}")
        self.dirty = True
        self.populate_slots(card)
        target = self.current_slot_index + 1
        self.current_slot_index = None
        self.slot_tree.selection_set(str(target))
        self.on_slot_select()
        self.refresh_image()

    def delete_challenge_slot(self) -> None:
        if not self.current_number or self.current_slot_index is None:
            messagebox.showinfo("删除骰槽", "请先选择一个挑战骰槽。")
            return
        card = self.by_number[self.current_number]
        self.push_undo(self.current_number, card)
        removed = card["挑战骰"]["槽位"].pop(self.current_slot_index)
        self.log_slot_change(
            card,
            "删除误识别骰槽",
            f"{removed.get('槽位UID')} 不是可放骰子的完整方框；以人工结论为准，最终数据中保持删除",
        )
        self.reindex_challenge_slots(card)
        self.current_slot_index = None
        self.dirty = True
        self.populate_slots(card)
        self.refresh_image()

    def populate_boost_slots(self, card: dict[str, Any]) -> None:
        self.boost_tree.delete(*self.boost_tree.get_children())
        boxes = card.get("小卡", {}).get("强化容量", {}).get("槽位坐标", [])
        for index, bbox in enumerate(boxes):
            self.boost_tree.insert("", "end", iid=str(index), values=(index + 1, json.dumps(bbox, ensure_ascii=False)))
        self.boost_coords.set("")

    def on_boost_select(self, _event: Any = None) -> None:
        selected = self.boost_tree.selection()
        if not selected or not self.current_number:
            return
        self.current_boost_index = int(selected[0])
        boxes = self.by_number[self.current_number].get("小卡", {}).get("强化容量", {}).get("槽位坐标", [])
        if self.current_boost_index < len(boxes):
            self.boost_coords.set(json.dumps(boxes[self.current_boost_index], ensure_ascii=False))
        self.current_slot_index = None
        self.refresh_image()

    def apply_boost_coords(self) -> None:
        if not self.current_number or self.current_boost_index is None:
            messagebox.showinfo("强化槽坐标", "请先选择一个强化点数槽。")
            return
        value = self.boost_coords.get().strip()
        try:
            parsed = json.loads(value) if value.startswith("[") else [float(part.strip()) for part in value.split(",")]
            if not isinstance(parsed, list) or len(parsed) != 4:
                raise ValueError
            bbox = [float(item) for item in parsed]
        except (ValueError, json.JSONDecodeError):
            messagebox.showerror("坐标格式错误", "坐标应为 [x1, y1, x2, y2]。")
            return
        card = self.by_number[self.current_number]
        self.push_undo(self.current_number, card)
        self.set_boost_bbox(card, self.current_boost_index, bbox)
        self.mark_card_revised(card)
        self.dirty = True
        self.populate_boost_slots(card)
        self.boost_tree.selection_set(str(self.current_boost_index))
        self.on_boost_select()

    def sync_boost_slots(self, card: dict[str, Any]) -> None:
        boxes = card.setdefault("小卡", {}).setdefault("强化容量", {}).setdefault("槽位坐标", [])
        capacity = len(boxes)
        small = card["小卡"]
        small["强化容量"]["当前值"] = capacity
        small["强化容量"]["视觉检测值"] = capacity
        reserve = small.setdefault("强化储备数（灰色方格）", {})
        reserve["当前值"] = capacity
        reserve["视觉检测值"] = capacity
        reserve["槽位坐标"] = deepcopy(boxes)
        reserve["说明"] = "卡面每个灰色方格可储备1点强化。"
        small.setdefault("强化", {})["强化容量"] = capacity
        raw_card = card.setdefault("原始结构化卡牌数据", {})
        components = raw_card.setdefault("components", {})
        components["boost_capacity"] = capacity
        components["boost_capacity_detected"] = capacity
        components["boost_slot_bboxes"] = deepcopy(boxes)
        self.small_capacity.set(str(capacity))

    def set_boost_bbox(self, card: dict[str, Any], index: int, bbox: list[float]) -> None:
        boxes = card.setdefault("小卡", {}).setdefault("强化容量", {}).setdefault("槽位坐标", [])
        if index >= len(boxes):
            return
        boxes[index] = [round(float(value), 2) for value in bbox]
        self.sync_boost_slots(card)

    def log_boost_change(self, card: dict[str, Any], operation: str, detail: str) -> None:
        self.mark_card_revised(card)
        review = card.setdefault("人工校对", {})
        changes = review.setdefault("强化槽修订记录", [])
        changes.append({"操作": operation, "说明": detail, "时间": datetime.now().astimezone().isoformat(timespec="seconds")})

    def add_boost_slot(self) -> None:
        if not self.current_number:
            return
        card = self.by_number[self.current_number]
        if not card.get("小卡", {}).get("是否小卡"):
            messagebox.showinfo("新增强化槽", "地点牌不使用小卡强化点数槽。")
            return
        self.push_undo(self.current_number, card)
        boxes = card.setdefault("小卡", {}).setdefault("强化容量", {}).setdefault("槽位坐标", [])
        boxes.append(self.default_bbox(0.075))
        self.sync_boost_slots(card)
        self.log_boost_change(card, "新增", f"新增强化槽 {len(boxes)}")
        self.dirty = True
        self.populate_boost_slots(card)
        self.current_boost_index = len(boxes) - 1
        self.boost_tree.selection_set(str(self.current_boost_index))
        self.on_boost_select()
        self.refresh_image()

    def delete_boost_slot(self) -> None:
        if not self.current_number or self.current_boost_index is None:
            messagebox.showinfo("删除强化槽", "请先选择一个强化点数槽。")
            return
        card = self.by_number[self.current_number]
        boxes = card.get("小卡", {}).get("强化容量", {}).get("槽位坐标", [])
        if self.current_boost_index >= len(boxes):
            return
        self.push_undo(self.current_number, card)
        removed_index = self.current_boost_index
        boxes.pop(removed_index)
        self.sync_boost_slots(card)
        self.log_boost_change(card, "删除", f"删除强化槽 {removed_index + 1}")
        self.current_boost_index = None
        self.dirty = True
        self.populate_boost_slots(card)
        self.refresh_image()

    def on_slot_select(self, _event: Any = None) -> None:
        if self.current_slot_index is not None:
            self.apply_slot(refresh=False)
        selected = self.slot_tree.selection()
        if not selected or not self.current_number:
            return
        self.current_slot_index = int(selected[0])
        self.current_boost_index = None
        slot = self.by_number[self.current_number]["挑战骰"]["槽位"][self.current_slot_index]
        self.slot_type.set(slot.get("槽位类型", {}).get("中文", ""))
        self.slot_skill.set(slot.get("技能类型", {}).get("中文", ""))
        self.slot_color.set(slot.get("技能颜色", {}).get("中文", ""))
        result_zh = slot.get("挑战骰结果要求", {}).get("中文", "") or "任意结果/未指定"
        self.slot_result.set("挫折/返回" if result_zh == "setback" else result_zh)
        ability_values = [item.get("中文", "") for item in slot.get("挑战骰能力要求", [])]
        self.select_listbox_values(self.slot_ability_list, ability_values)
        self.slot_terrain.set(slot.get("挑战骰地形", {}).get("中文", "") or "未指定/未识别")
        self.slot_interaction.set(bool(slot.get("挑战骰互动元素", {}).get("是否互动通配槽")))
        self.slot_cost.set(str(slot.get("挑战骰强化花费", 0)))
        self.slot_gain.set(str(slot.get("挑战骰强化生成", 0)))
        self.slot_impact.set(bool(slot.get("挑战骰闪电标志")))
        self.slot_modifier.set(str(slot.get("额外投骰数量", 0)))
        self.slot_action.set(slot.get("特定行动", ""))
        self.slot_coords.set(str(slot.get("坐标_原图像素", "")))
        self.refresh_image()

    def apply_slot(self, refresh: bool = True) -> None:
        if self.current_slot_index is None or not self.current_number:
            return
        card = self.by_number[self.current_number]
        slot = card["挑战骰"]["槽位"][self.current_slot_index]
        before_card = deepcopy(card)
        before = json.dumps(slot, ensure_ascii=False, sort_keys=True)
        type_zh = self.slot_type.get()
        skill_zh = self.slot_skill.get()
        color_zh = self.slot_color.get()
        result_zh = self.slot_result.get()
        terrain_zh = self.slot_terrain.get()
        slot.setdefault("槽位类型", {}).update({"中文": type_zh, "原值": SLOT_TYPE_CODES.get(type_zh, "manual")})
        slot.setdefault("技能类型", {}).update({"中文": skill_zh, "原值": SKILL_CODES.get(skill_zh, "")})
        slot.setdefault("技能颜色", {}).update({"中文": color_zh, "原值": COLOR_CODES.get(color_zh, "")})
        slot.setdefault("挑战骰结果要求", {}).update({"中文": result_zh, "原值": RESULT_CODES.get(result_zh, "")})
        selected_abilities = self.selected_listbox_values(self.slot_ability_list)
        slot["挑战骰能力要求"] = [{"原值": SKILL_CODES.get(value, ""), "中文": value} for value in selected_abilities]
        slot.setdefault("挑战骰地形", {}).update({"中文": terrain_zh, "原值": "" if terrain_zh == "未指定/未识别" else terrain_zh})
        slot.setdefault("挑战骰互动元素", {})["是否互动通配槽"] = self.slot_interaction.get()
        slot["挑战骰强化花费"] = parse_int(self.slot_cost.get()) or 0
        slot["挑战骰强化生成"] = parse_int(self.slot_gain.get()) or 0
        slot["挑战骰闪电标志"] = self.slot_impact.get()
        slot["额外投骰数量"] = parse_int(self.slot_modifier.get()) or 0
        slot["特定行动"] = self.slot_action.get()
        coordinate_text = self.slot_coords.get().strip()
        if coordinate_text:
            try:
                parsed = json.loads(coordinate_text) if coordinate_text.startswith("[") else [float(value.strip()) for value in coordinate_text.split(",")]
                if isinstance(parsed, list) and len(parsed) == 4:
                    self.set_slot_bbox(card, self.current_slot_index, [float(value) for value in parsed])
                    self.slot_coords.set(str(slot.get("坐标_原图像素", "")))
            except (ValueError, json.JSONDecodeError):
                if refresh:
                    messagebox.showerror("坐标格式错误", "坐标应为 [x1, y1, x2, y2]，例如 [500, 100, 840, 420]。")
                return
        if card.get("小卡", {}).get("是否小卡"):
            card["小卡"]["挑战骰"]["槽位"] = card["挑战骰"]["槽位"]
        changed = before != json.dumps(slot, ensure_ascii=False, sort_keys=True)
        if changed:
            self.push_undo(self.current_number, before_card)
            if refresh or self.user_modified_current:
                self.mark_card_revised(card)
        self.dirty = self.dirty or changed
        if refresh:
            selected = self.current_slot_index
            self.populate_slots(card)
            self.slot_tree.selection_set(str(selected))
            self.refresh_image()
            self.status_var.set("已应用骰槽修改，尚未写入磁盘")

    def commit_current(self) -> None:
        if not self.current_number or self.loading:
            return
        self.apply_action(refresh=False)
        self.apply_slot(refresh=False)
        card = self.by_number[self.current_number]
        before_card = deepcopy(card)
        before = json.dumps(card, ensure_ascii=False, sort_keys=True)
        if not card.get("地图", {}).get("是否地点牌", False):
            card.setdefault("名字", {})["人工修订值"] = self.base_name.get().strip()
        card.setdefault("基础文本描述", {})["人工修订值"] = text_get(self.base_text).strip()
        value = parse_int(self.base_value.get())
        card.setdefault("基础信息", {}).setdefault("价值", {})["当前值"] = value
        elements = card.setdefault("地图", {}).setdefault("地图元素", {})
        elements["地形主类型"] = self.map_terrain.get().strip() or None
        elements["地形标签"] = self.selected_listbox_values(self.map_tags_list)
        elements["地区标记"] = self.map_region.get().strip() or None
        marker_text = self.map_marker.get().strip()
        try:
            elements["元素或区域标记"] = json.loads(marker_text) if marker_text.startswith(("{", "[")) else marker_text or None
        except json.JSONDecodeError:
            elements["元素或区域标记"] = marker_text
        roads = card["地图"].setdefault("上下左右道路罗盘", {})
        for key, _label in DIR_KEYS:
            road = roads.setdefault(key, {})
            vars_ = self.road_vars[key]
            road["道路状态"] = vars_["state"].get()
            road["罗盘印刷值"] = vars_["printed"].get().strip() or None
            destination = vars_["destination"].get().strip()
            road["目标地点编号"] = destination.zfill(4) if destination.isdigit() else destination or None
            road["移动花费"] = parse_cost(vars_["cost"].get())
        small = card.setdefault("小卡", {})
        small_type_zh = self.small_type.get().strip() or "未分类/不适用"
        small.setdefault("小卡类型", {}).update({"中文": small_type_zh, "原值": SMALL_TYPE_CODES.get(small_type_zh, "")})
        card.setdefault("基础信息", {}).setdefault("小卡类型", {}).update({"中文": small_type_zh, "原值": SMALL_TYPE_CODES.get(small_type_zh, "")})
        reserve_value = parse_int(self.small_reserve.get())
        small.setdefault("文字规则中的储备容量", {})["当前值"] = reserve_value
        small.setdefault("储备容量", {})["当前值"] = reserve_value
        small_value = parse_int(self.small_value.get())
        small.setdefault("价格", {})["当前值"] = small_value
        small.setdefault("价值", {})["当前值"] = small_value
        if small.get("是否小卡"):
            unified_value = small_value if small_value is not None else value
            small["价格"]["当前值"] = unified_value
            small["价值"]["当前值"] = unified_value
            card["基础信息"]["价值"]["当前值"] = unified_value
        small.setdefault("放置时强化点数", {})["当前值"] = parse_int(self.small_initial.get())
        capacity_value = parse_int(self.small_capacity.get()) or 0
        small.setdefault("强化储备数（灰色方格）", {})["当前值"] = capacity_value
        small.setdefault("强化容量", {})["当前值"] = capacity_value
        small.setdefault("强化", {})["放置时强化点数"] = small["放置时强化点数"]["当前值"]
        small["强化"]["强化容量"] = small["强化容量"]["当前值"]
        small["人工可读放置效果修订"] = [line.strip() for line in text_get(self.small_placement).splitlines() if line.strip()]
        small["人工可读技能效果修订"] = [line.strip() for line in text_get(self.small_skills).splitlines() if line.strip()]
        small["人工可读技能花销修订"] = [line.strip() for line in text_get(self.small_costs).splitlines() if line.strip()]
        review = card.setdefault("人工校对", {})
        review["总状态"] = self.review_status.get()
        review["问题列表"] = [line.strip() for line in text_get(self.review_issues).splitlines() if line.strip()]
        review["备注"] = text_get(self.review_notes).strip()
        previous_prompt = str(review.get("待AI处理提示词") or "").strip()
        ai_prompt = text_get(self.review_ai_prompt).strip()
        if ai_prompt:
            review["待AI处理提示词"] = ai_prompt
            if ai_prompt != previous_prompt or not review.get("AI处理状态"):
                review["AI处理状态"] = "待处理"
                review["提示词最后修改时间"] = datetime.now().astimezone().isoformat(timespec="seconds")
                review.pop("AI处理结果摘要", None)
                review.pop("AI处理完成时间", None)
        else:
            review.pop("待AI处理提示词", None)
            review.pop("AI处理状态", None)
            review.pop("提示词最后修改时间", None)
            review.pop("AI处理结果摘要", None)
            review.pop("AI处理完成时间", None)
        card["牌面总结"] = build_card_summary(
            card,
            self.abilities_by_card.get(int(card.get("编号", 0) or 0), []),
        )
        self.review_summary.configure(state="normal")
        text_set(self.review_summary, card["牌面总结"]["内容"])
        self.review_summary.configure(state="disabled")
        ai_status = str(review.get("AI处理状态") or "")
        self.review_ai_status.set(
            f"AI处理状态：{ai_status}｜{review.get('AI处理结果摘要', '')}" if ai_status else "未填写提示词"
        )
        after = json.dumps(card, ensure_ascii=False, sort_keys=True)
        if before != after:
            self.push_undo(self.current_number, before_card)
            if self.user_modified_current:
                self.mark_card_revised(card)
            review["最后人工编辑时间"] = datetime.now().astimezone().isoformat(timespec="seconds")
            self.dirty = True
            self.status_var.set(f"{self.current_number} 已修改，尚未写入磁盘")
        self.user_modified_current = False
        self.refresh_advanced()

    def regenerate_current_summary(self) -> None:
        if not self.current_number:
            return
        self.commit_current()
        card = self.by_number[self.current_number]
        before = deepcopy(card.get("牌面总结"))
        card["牌面总结"] = build_card_summary(
            card,
            self.abilities_by_card.get(int(card.get("编号", 0) or 0), []),
        )
        self.review_summary.configure(state="normal")
        text_set(self.review_summary, card["牌面总结"]["内容"])
        self.review_summary.configure(state="disabled")
        if before != card["牌面总结"]:
            self.mark_card_revised(card)
            self.dirty = True
        self.status_var.set(f"卡牌 {self.current_number} 的牌面总结已按当前数据重新生成")

    def mark_verified(self) -> None:
        self.mark_review_status("已核验")

    def mark_review_status(self, status: str) -> None:
        """从任意标签页快速设置当前卡牌的校对状态。"""
        if not self.current_number:
            return
        if status == "未修订":
            self.mark_current_unrevised()
            self.review_status.set("未校对")
            self.commit_current()
        else:
            self.review_status.set(status)
            self.user_modified_current = True
            self.commit_current()
        self.filter_cards()
        self.status_var.set(f"卡牌 {self.current_number} 已标记为{status}")

    def refresh_advanced(self) -> None:
        if not self.current_number:
            return
        self.advanced_text.delete("1.0", tk.END)
        self.advanced_text.insert("1.0", json.dumps(self.by_number[self.current_number], ensure_ascii=False, indent=2))

    def apply_advanced(self) -> None:
        if not self.current_number:
            return
        try:
            card = json.loads(text_get(self.advanced_text))
        except json.JSONDecodeError as exc:
            messagebox.showerror("JSON格式错误", f"第{exc.lineno}行，第{exc.colno}列：{exc.msg}")
            return
        if str(card.get("编号")) != self.current_number:
            messagebox.showerror("编号不一致", "高级JSON中的编号不能修改。")
            return
        self.push_undo(self.current_number, self.by_number[self.current_number])
        self.by_number[self.current_number] = card
        for i, item in enumerate(self.cards):
            if str(item.get("编号")) == self.current_number:
                self.cards[i] = card
                break
        self.dirty = True
        self.mark_card_revised(card)
        self.load_card(card)

    def resolve_image(self, card: dict[str, Any]) -> Path | None:
        candidates = [card.get("基础信息", {}).get("中文源图片"), card.get("原始结构化卡牌数据", {}).get("source_image")]
        texture = card.get("基础信息", {}).get("贴图资源")
        if texture and str(texture).startswith("res://"):
            candidates.append(PROJECT / str(texture)[6:])
        folder = "大卡" if card.get("地图", {}).get("是否地点牌") else "标准卡"
        candidates.append(PROJECT / "正式素材" / folder / f"{card.get('编号')}.jpg")
        for value in candidates:
            if value and Path(value).exists():
                return Path(value)
        return None

    def refresh_image(self) -> None:
        if not self.current_number:
            return
        card = self.by_number[self.current_number]
        path = self.resolve_image(card)
        self.canvas.delete("all")
        if not path:
            self.canvas.create_text(300, 300, text="找不到中文卡图", fill="white")
            return
        try:
            image = Image.open(path).convert("RGB")
        except Exception as exc:
            self.canvas.create_text(300, 300, text=f"图片读取失败\n{exc}", fill="white")
            return
        self.image_source = image
        annotated = image.copy()
        if self.overlay_var.get():
            draw = ImageDraw.Draw(annotated)
            for i, slot in enumerate(card.get("挑战骰", {}).get("槽位", [])):
                bbox = slot.get("坐标_原图像素")
                if not bbox or len(bbox) != 4:
                    continue
                box = tuple(int(v) for v in bbox)
                selected = i == self.current_slot_index
                color = "#ff3d3d" if selected else "#00e5ff"
                width = 14 if selected else 8
                draw.rectangle(box, outline=color, width=width)
                draw.text((box[0] + 8, box[1] + 8), str(slot.get("槽位序号", i + 1)), fill=color)
            for i, bbox in enumerate(card.get("小卡", {}).get("强化容量", {}).get("槽位坐标", [])):
                if len(bbox) == 4:
                    box = tuple(int(v) for v in bbox)
                    selected = i == self.current_boost_index
                    color = "#ff00d4" if selected else "#ffd740"
                    draw.rectangle(box, outline=color, width=14 if selected else 8)
                    draw.text((box[0] + 8, box[1] + 8), f"强{i + 1}", fill=color)
                    if selected and self.slot_edit_var.get():
                        handle = max(18, min(image.width, image.height) // 100)
                        for hx, hy in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])):
                            draw.rectangle((hx - handle, hy - handle, hx + handle, hy + handle), fill="#ffeb3b", outline="#231f20", width=4)
            marker_kind = str(card.get("卡面标记槽", {}).get("类型") or "标")
            for i, bbox in enumerate(card.get("卡面标记槽", {}).get("槽位坐标", [])):
                if len(bbox) == 4:
                    box = tuple(int(v) for v in bbox)
                    color = "#b388ff"
                    draw.rectangle(box, outline=color, width=8)
                    draw.text((box[0] + 8, box[1] + 8), f"{marker_kind[:1]}{i + 1}", fill=color)
        rotated = annotated.rotate(-self.image_rotation, expand=True)
        cw, ch = max(200, self.canvas.winfo_width() - 20), max(200, self.canvas.winfo_height() - 20)
        fit = min(cw / rotated.width, ch / rotated.height)
        scale = fit * self.image_zoom
        self.display_scale = scale
        resized = rotated.resize((max(1, int(rotated.width * scale)), max(1, int(rotated.height * scale))), Image.Resampling.LANCZOS)
        self.image_tk = ImageTk.PhotoImage(resized)
        offset_x = max(0.0, (cw - resized.width) / 2.0)
        offset_y = max(0.0, (ch - resized.height) / 2.0)
        self.display_offset = (offset_x, offset_y)
        self.canvas.create_image(offset_x, offset_y, image=self.image_tk, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, max(cw, resized.width), max(ch, resized.height)))

    def change_zoom(self, factor: float) -> None:
        self.image_zoom = max(0.35, min(8.0, self.image_zoom * factor))
        self.refresh_image()

    def reset_zoom(self) -> None:
        self.image_zoom = 1.0
        self.refresh_image()

    def set_actual_size(self) -> None:
        if self.image_source is None:
            return
        width, height = self.image_source.size
        if self.image_rotation in (90, 270):
            width, height = height, width
        canvas_width = max(200, self.canvas.winfo_width() - 20)
        canvas_height = max(200, self.canvas.winfo_height() - 20)
        fit = min(canvas_width / width, canvas_height / height)
        self.image_zoom = max(0.35, min(8.0, 1.0 / max(fit, 0.001)))
        self.refresh_image()

    def toggle_image_focus(self) -> None:
        self.image_focus_mode = not self.image_focus_mode
        if self.image_focus_mode:
            self.card_list_frame.grid_remove()
            self.editor_frame.grid_remove()
            self.image_frame.grid_configure(column=0, columnspan=3)
            self.image_focus_label.set("返回校对布局")
        else:
            self.image_frame.grid_configure(column=1, columnspan=1)
            self.card_list_frame.grid()
            self.editor_frame.grid()
            self.image_focus_label.set("大图预览")
        self.after_idle(self.refresh_image)

    def maximize_window(self) -> None:
        try:
            self.state("zoomed")
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                pass

    def check_for_updates(self, silent: bool = False) -> None:
        if self.update_check_running:
            if not silent:
                self.status_var.set("正在检查更新，请稍候…")
            return
        self.update_check_running = True
        if not silent:
            self.status_var.set("正在连接 GitHub 检查更新…")

        def worker() -> None:
            try:
                release = fetch_latest_release()
                self.after(0, lambda: self.handle_update_release(release, silent))
            except Exception as exc:
                detail = str(exc)
                self.after(0, lambda detail=detail: self.handle_update_error(detail, silent))

        threading.Thread(target=worker, name="editor-update-check", daemon=True).start()

    def handle_update_error(self, detail: str, silent: bool) -> None:
        self.update_check_running = False
        self.status_var.set("更新检查失败；可稍后点击“检查更新”重试")
        if not silent:
            messagebox.showerror("检查更新失败", detail)

    def handle_update_release(self, release: dict[str, Any], silent: bool) -> None:
        self.update_check_running = False
        latest = str(release.get("tag_name") or "")
        if version_numbers(latest) <= version_numbers(EDITOR_VERSION):
            self.status_var.set(f"当前已是最新版本 v{EDITOR_VERSION}")
            if not silent:
                messagebox.showinfo("检查更新", f"当前版本 v{EDITOR_VERSION} 已是最新版本。")
            return
        notes = str(release.get("body") or "").strip()
        summary = f"发现新版本 {latest}，当前版本 v{EDITOR_VERSION}。"
        if notes:
            summary += f"\n\n{notes[:1200]}"
        if messagebox.askyesno("发现编辑器更新", summary + "\n\n是否立即下载并安装？"):
            self.download_and_install_update(release)
        else:
            self.status_var.set(f"已有新版本 {latest}，可点击“检查更新”安装")

    def download_and_install_update(self, release: dict[str, Any]) -> None:
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            asset_name = WINDOWS_UPDATE_ASSET
            target = Path(sys.executable).resolve()
        elif os.environ.get("APPIMAGE") or os.environ.get("CARD_AUDIT_APPIMAGE_PATH"):
            asset_name = LINUX_UPDATE_ASSET
            target = Path(os.environ.get("APPIMAGE") or os.environ["CARD_AUDIT_APPIMAGE_PATH"]).resolve()
        else:
            messagebox.showinfo("无法自动安装", "源码运行模式不会替换程序文件，请使用发布版 EXE 或 AppImage。")
            return
        assets = {str(item.get("name")): item for item in release.get("assets", []) if isinstance(item, dict)}
        asset = assets.get(asset_name)
        checksum_asset = assets.get(UPDATE_CHECKSUM_ASSET)
        if not asset or not checksum_asset:
            messagebox.showerror("更新包不完整", f"GitHub Release 缺少 {asset_name} 或 {UPDATE_CHECKSUM_ASSET}。")
            return
        self.status_var.set(f"正在下载 {asset_name}…")

        def worker() -> None:
            downloaded = target.with_name(f".{target.name}.update")
            try:
                checksum_bytes = download_url(str(checksum_asset["browser_download_url"])) or b""
                checksums = {}
                for line in checksum_bytes.decode("utf-8").splitlines():
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2:
                        checksums[parts[1].lstrip("*")] = parts[0].lower()
                expected = checksums.get(asset_name)
                if not expected:
                    raise RuntimeError(f"校验文件中没有 {asset_name}")
                download_url(str(asset["browser_download_url"]), downloaded)
                actual = hashlib.sha256(downloaded.read_bytes()).hexdigest().lower()
                if actual != expected:
                    raise RuntimeError(f"SHA256 校验失败：期望 {expected}，实际 {actual}")
                self.after(0, lambda: self.install_downloaded_update(target, downloaded))
            except Exception as exc:
                downloaded.unlink(missing_ok=True)
                detail = str(exc)
                self.after(0, lambda detail=detail: self.handle_update_error(detail, False))

        threading.Thread(target=worker, name="editor-update-download", daemon=True).start()

    def install_downloaded_update(self, target: Path, downloaded: Path) -> None:
        try:
            if sys.platform == "win32":
                self.schedule_windows_update(target, downloaded)
                messagebox.showinfo("更新已下载", "编辑器将退出、替换 EXE，然后自动重新打开。")
                self.destroy()
                return
            downloaded.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            os.replace(downloaded, target)
            self.status_var.set(f"AppImage 已更新到 {target.name}")
            if messagebox.askyesno("更新完成", "AppImage 已安全替换，JSON 和图片未改动。是否立即重启？"):
                environment = independent_frozen_process_environment()
                environment.pop("APPIMAGE", None)
                environment["APPIMAGE_EXTRACT_AND_RUN"] = "1"
                environment["CARD_AUDIT_APPIMAGE_PATH"] = str(target)
                subprocess.Popen([str(target), str(self.path)], env=environment, start_new_session=True)
                self.destroy()
        except Exception as exc:
            downloaded.unlink(missing_ok=True)
            messagebox.showerror("安装更新失败", str(exc))

    def schedule_windows_update(self, target: Path, downloaded: Path) -> None:
        def ps_quote(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        script_path = target.with_name(f".{target.stem}.update.ps1")
        legacy_launcher_path = target.with_name(f".{target.stem}.update.cmd")
        script = f"""$ErrorActionPreference = 'Continue'
$target = {ps_quote(str(target))}
$downloaded = {ps_quote(str(downloaded))}
$dataFile = {ps_quote(str(self.path))}
$processId = {os.getpid()}
$log = $target + '.update.log'
'Updater started' | Out-File -LiteralPath $log -Encoding utf8
for ($wait = 0; $wait -lt 120; $wait++) {{
    if (-not (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {{ break }}
    Start-Sleep -Milliseconds 250
}}
$updated = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {{
    try {{
        Copy-Item -LiteralPath $downloaded -Destination $target -Force -ErrorAction Stop
        if ((Get-Item -LiteralPath $target).Length -eq (Get-Item -LiteralPath $downloaded).Length) {{
            $updated = $true
            break
        }}
    }} catch {{
        $_ | Out-File -LiteralPath $log -Append -Encoding utf8
    }}
    Start-Sleep -Milliseconds 500
}}
if (-not $updated) {{
    'EXE replacement failed after retries' | Out-File -LiteralPath $log -Append -Encoding utf8
    exit 1
}}
Remove-Item -LiteralPath $downloaded -Force -ErrorAction SilentlyContinue
Start-Process -FilePath $target -ArgumentList @($dataFile) -WorkingDirectory (Split-Path -Parent $target) -WindowStyle Hidden
Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath {ps_quote(str(legacy_launcher_path))} -Force -ErrorAction SilentlyContinue
"""
        script_path.write_text(script, encoding="utf-8-sig")
        launch_windows_update_script(script_path)

    def rotate_image(self, delta: int) -> None:
        if not self.current_number:
            return
        self.push_undo(self.current_number, self.by_number[self.current_number])
        self.image_rotation = (self.image_rotation + delta) % 360
        self.by_number[self.current_number].setdefault("人工校对", {})["图片显示旋转度数"] = self.image_rotation
        self.mark_card_revised(self.by_number[self.current_number])
        self.dirty = True
        self.refresh_image()
        self.status_var.set(f"卡牌 {self.current_number} 显示方向：{self.image_rotation}°，尚未写入磁盘")

    def reset_rotation(self) -> None:
        if not self.current_number:
            return
        self.push_undo(self.current_number, self.by_number[self.current_number])
        self.image_rotation = 0
        self.by_number[self.current_number].setdefault("人工校对", {})["图片显示旋转度数"] = 0
        self.mark_card_revised(self.by_number[self.current_number])
        self.dirty = True
        self.refresh_image()

    def on_mousewheel(self, event: tk.Event) -> None:
        self.change_zoom(1.12 if event.delta > 0 else 0.89)

    def display_to_original(self, event_x: float, event_y: float) -> tuple[float, float] | None:
        if not self.current_number or self.display_scale <= 0:
            return None
        offset_x, offset_y = self.display_offset
        x = (self.canvas.canvasx(event_x) - offset_x) / self.display_scale
        y = (self.canvas.canvasy(event_y) - offset_y) / self.display_scale
        if self.image_source is None:
            return None
        width, height = self.image_source.size
        if self.image_rotation == 90:
            x, y = y, height - 1 - x
        elif self.image_rotation == 180:
            x, y = width - 1 - x, height - 1 - y
        elif self.image_rotation == 270:
            x, y = width - 1 - y, x
        return x, y

    def on_image_press(self, event: tk.Event) -> None:
        """点击选中槽位；调整模式下开始移动或缩放。"""
        self.canvas.focus_set()
        point = self.display_to_original(event.x, event.y)
        if point is None or not self.current_number:
            return
        x, y = point
        card = self.by_number[self.current_number]
        slots = card.get("挑战骰", {}).get("槽位", [])
        hit_index = None
        for index, slot in reversed(list(enumerate(slots))):
            bbox = slot.get("坐标_原图像素")
            if bbox and len(bbox) == 4 and bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]:
                hit_index = index
                break
        hit_kind = "challenge"
        if hit_index is None:
            boxes = card.get("小卡", {}).get("强化容量", {}).get("槽位坐标", [])
            for index, bbox in reversed(list(enumerate(boxes))):
                if bbox and len(bbox) == 4 and bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]:
                    hit_index = index
                    hit_kind = "boost"
                    break
        if hit_index is None:
            self.slot_drag = None
            return
        if hit_kind == "challenge":
            self.current_boost_index = None
            self.open_editor_tab("挑战骰槽")
            self.slot_tree.selection_set(str(hit_index))
            self.slot_tree.focus(str(hit_index))
            self.slot_tree.see(str(hit_index))
            self.on_slot_select()
            bbox = list(slots[hit_index].get("坐标_原图像素"))
        else:
            self.current_slot_index = None
            self.open_editor_tab("小卡/强化/技能")
            self.boost_tree.selection_set(str(hit_index))
            self.boost_tree.focus(str(hit_index))
            self.boost_tree.see(str(hit_index))
            self.on_boost_select()
            bbox = list(card["小卡"]["强化容量"]["槽位坐标"][hit_index])
        threshold = max(18.0, 14.0 / max(self.display_scale, 0.01))
        corners = {
            "nw": (bbox[0], bbox[1]), "ne": (bbox[2], bbox[1]),
            "se": (bbox[2], bbox[3]), "sw": (bbox[0], bbox[3]),
        }
        mode = "move"
        if hit_kind == "boost":
            for name, (cx, cy) in corners.items():
                if abs(x - cx) <= threshold and abs(y - cy) <= threshold:
                    mode = name
                    break
        self.slot_drag = {"kind": hit_kind, "index": hit_index, "mode": mode, "start": (x, y), "bbox": bbox, "undo_pushed": False}

    def on_image_drag(self, event: tk.Event) -> None:
        if not self.slot_drag or not self.current_number or self.image_source is None:
            return
        point = self.display_to_original(event.x, event.y)
        if point is None:
            return
        x, y = point
        if not self.slot_drag.get("undo_pushed"):
            self.push_undo(self.current_number, self.by_number[self.current_number])
            self.slot_drag["undo_pushed"] = True
        start_x, start_y = self.slot_drag["start"]
        x1, y1, x2, y2 = self.slot_drag["bbox"]
        dx, dy = x - start_x, y - start_y
        mode = self.slot_drag["mode"]
        if mode == "move":
            x1, x2, y1, y2 = x1 + dx, x2 + dx, y1 + dy, y2 + dy
        else:
            if "w" in mode:
                x1 += dx
            if "e" in mode:
                x2 += dx
            if "n" in mode:
                y1 += dy
            if "s" in mode:
                y2 += dy
        width, height = self.image_source.size
        min_size = 24.0
        if x2 - x1 < min_size:
            x2 = x1 + min_size
        if y2 - y1 < min_size:
            y2 = y1 + min_size
        box_width, box_height = x2 - x1, y2 - y1
        x1 = max(0.0, min(x1, width - box_width))
        y1 = max(0.0, min(y1, height - box_height))
        x2, y2 = x1 + box_width, y1 + box_height
        card = self.by_number[self.current_number]
        new_bbox = [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)]
        self.mark_card_revised(card)
        if self.slot_drag.get("kind") == "boost":
            self.set_boost_bbox(card, int(self.slot_drag["index"]), new_bbox)
            self.boost_coords.set(json.dumps(new_bbox, ensure_ascii=False))
        else:
            self.set_slot_bbox(card, int(self.slot_drag["index"]), new_bbox)
            self.slot_coords.set(json.dumps(new_bbox, ensure_ascii=False))
        self.dirty = True
        self.refresh_image()

    def on_image_release(self, _event: tk.Event) -> None:
        if self.slot_drag and self.current_number:
            card = self.by_number[self.current_number]
            if self.slot_drag.get("kind") == "boost":
                selected = int(self.slot_drag["index"])
                boxes = card.get("小卡", {}).get("强化容量", {}).get("槽位坐标", [])
                if self.slot_drag.get("undo_pushed") and selected < len(boxes):
                    self.log_boost_change(
                        card,
                        "人工调整位置",
                        f"强化槽 {selected + 1} 最终位置：{boxes[selected]}",
                    )
                self.populate_boost_slots(card)
                if selected < len(card.get("小卡", {}).get("强化容量", {}).get("槽位坐标", [])):
                    self.current_boost_index = selected
                    self.boost_tree.selection_set(str(selected))
                    self.on_boost_select()
            else:
                selected = int(self.slot_drag["index"])
                slots = card.get("挑战骰", {}).get("槽位", [])
                if self.slot_drag.get("undo_pushed") and selected < len(slots):
                    slot = slots[selected]
                    self.log_slot_change(
                        card,
                        "人工调整位置",
                        f"{slot.get('槽位UID')} 最终位置：{slot.get('坐标_原图像素')}",
                    )
                self.populate_slots(card)
                if selected < len(card.get("挑战骰", {}).get("槽位", [])):
                    self.current_slot_index = None
                    self.slot_tree.selection_set(str(selected))
                    self.on_slot_select()
            self.status_var.set(f"卡牌 {self.current_number} 槽位坐标已调整，尚未写入磁盘")
        self.slot_drag = None

    def set_slot_bbox(self, card: dict[str, Any], index: int, bbox: list[float]) -> None:
        bbox = self.fixed_challenge_bbox(bbox)
        if self.image_source is not None:
            width, height = self.image_source.size
        else:
            width = height = 1
        normalized = [round(bbox[0] / width, 6), round(bbox[1] / height, 6), round(bbox[2] / width, 6), round(bbox[3] / height, 6)]
        slot = card["挑战骰"]["槽位"][index]
        slot["坐标_原图像素"] = bbox
        slot["坐标_归一化"] = normalized
        raw = slot.get("原始槽位数据")
        if isinstance(raw, dict):
            raw["bbox"] = bbox
            raw["bbox_normalized"] = normalized
        small_slots = card.get("小卡", {}).get("挑战骰", {}).get("槽位", [])
        if index < len(small_slots):
            small_slots[index]["坐标_原图像素"] = bbox
            small_slots[index]["坐标_归一化"] = normalized
            small_raw = small_slots[index].get("原始槽位数据")
            if isinstance(small_raw, dict):
                small_raw["bbox"] = bbox
                small_raw["bbox_normalized"] = normalized

    def select_relative(self, delta: int) -> None:
        selected = self.card_list.curselection()
        if not selected or not self.card_list.size():
            return
        target = max(0, min(self.card_list.size() - 1, selected[0] + delta))
        self.card_list.selection_clear(0, tk.END)
        self.card_list.selection_set(target)
        self.card_list.see(target)
        self.on_card_select()

    def export_ai_prompt_cards(self) -> None:
        """导出待处理提示词及完整卡牌对象，供后续集中校对并同步代码。"""
        self.commit_current()
        prompted_cards = [
            deepcopy(card)
            for card in self.cards
            if str(card.get("人工校对", {}).get("待AI处理提示词") or "").strip()
            and str(card.get("人工校对", {}).get("AI处理状态") or "") != "已完成"
        ]
        if not prompted_cards:
            messagebox.showinfo("导出待AI任务包", "当前没有尚待处理的纠正提示词。")
            return
        default_name = self.path.stem + ".待AI校对任务包.json"
        target = filedialog.asksaveasfilename(
            title="导出待AI校对任务包",
            initialdir=str(self.path.parent),
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not target:
            return
        payload = {
            "处理协议版本": 1,
            "用途": "仅包含尚未完成的纠正提示词；AI须完成卡图人工校对并同步修改派生数据、运行代码和测试。",
            "处理要求": [
                "每张卡以人工校对.待AI处理提示词为最高优先级，并与最终中文卡图逐项核对。",
                "纠正牌面总结以及所有重复保存的结构化卡牌字段，避免只改展示文字。",
                "同步修改runtime_cards、card_abilities及其生成器或权威覆盖源，确保重新生成后不会回退。",
                "涉及游戏规则时修改对应运行代码并添加或更新回归测试。",
                "完成后将AI处理状态设为已完成，写入AI处理结果摘要、完成时间和修改目标；保留原提示词供追溯。",
            ],
            "来源文件": str(self.path),
            "导出时间": datetime.now().astimezone().isoformat(timespec="seconds"),
            "待处理数量": len(prompted_cards),
            "卡牌": prompted_cards,
        }
        try:
            Path(target).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.status_var.set(f"已导出 {len(prompted_cards)} 张待AI校对卡片：{Path(target).name}")
        messagebox.showinfo("导出完成", f"已导出 {len(prompted_cards)} 张待AI校对卡片。\n\n{target}")

    def export_all_card_summaries(self) -> None:
        """导出全部大卡、小卡与交锋卡的简明总结。"""
        self.commit_current()
        default_name = self.path.stem + ".全部牌面总结.json"
        target = filedialog.asksaveasfilename(
            title="导出全部牌面总结",
            initialdir=str(self.path.parent),
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not target:
            return
        counts = {"大卡": 0, "小卡": 0, "交锋卡": 0}
        cards = []
        for card in self.cards:
            kind = card_kind(card)
            counts[kind] += 1
            summary = card.get("牌面总结", {})
            cards.append({
                "编号": str(card.get("编号", "")).zfill(4),
                "卡牌类别": kind,
                "标题": summary.get("标题", ""),
                "牌面总结": summary.get("内容", ""),
                "人工核验优先级": summary.get("人工核验优先级", "常规"),
                "人工核验状态": summary.get("人工核验状态", "未校对"),
                "人工核验原因": summary.get("人工核验原因", ""),
                "人工核验重点": summary.get("人工核验重点", []),
                "能力来源": summary.get("能力来源", []),
                "中文源图片": card.get("基础信息", {}).get("中文源图片", ""),
                "AI处理状态": card.get("人工校对", {}).get("AI处理状态", ""),
            })
        payload = {
            "结构版本": SUMMARY_SCHEMA_VERSION,
            "用途": "全部大卡、小卡、交锋卡的牌面元素与功能总结，供人工核对与提示词纠正。",
            "导出时间": datetime.now().astimezone().isoformat(timespec="seconds"),
            "总数": len(cards),
            "分类统计": counts,
            "人工核验优先级统计": dict(Counter(str(card.get("人工核验优先级", "常规")) for card in cards)),
            "卡牌": cards,
        }
        try:
            Path(target).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.status_var.set(f"已导出全部 {len(cards)} 份牌面总结：{Path(target).name}")
        messagebox.showinfo("导出完成", f"已导出全部 {len(cards)} 份牌面总结。\n\n{target}")

    def save_all(self) -> None:
        self.commit_current()
        self.document["卡牌"] = self.cards
        try:
            if not self.backup_done and self.path.exists():
                backup = self.path.with_name(self.path.stem + ".首次编辑备份.json")
                if not backup.exists():
                    shutil.copy2(self.path, backup)
                self.backup_done = True
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp.replace(self.path)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.pending_revision_numbers.clear()
        self.dirty = False
        self.populate_list()
        self.status_var.set(f"已安全写入 {self.path.name}｜{len(self.cards)} 张卡")
        if self.remote_sync and self.remote_sync.get("auto_sync", True):
            self.sync_remote(notify=False)

    def open_document(self) -> None:
        path = filedialog.askopenfilename(title="打开校对JSON", filetypes=[("JSON", "*.json")])
        if path:
            self.load_document_async(Path(path), None)

    def on_close(self) -> None:
        if self.remote_sync_running:
            self.closing = True
            self.status_var.set("正在完成远程同步，完成后退出…")
            return
        self.commit_current()
        if self.dirty:
            answer = messagebox.askyesnocancel("有未保存修改", "是否保存到磁盘后退出？")
            if answer is None:
                return
            if answer:
                self.closing = True
                self.save_all()
                if self.remote_sync and self.remote_sync_running:
                    return
        self.destroy()


def resume_pending_windows_update() -> bool:
    """恢复上次已下载但未执行的 Windows 更新，避免更新文件永久留在目录。"""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    target = Path(sys.executable).resolve()
    downloaded = target.with_name(f".{target.name}.update")
    script_path = target.with_name(f".{target.stem}.update.ps1")
    legacy_launcher_path = target.with_name(f".{target.stem}.update.cmd")
    if not downloaded.exists() or not script_path.exists():
        return False
    try:
        # 只恢复新格式脚本，避免旧版本残留包被误执行造成版本回退。
        if "'Updater started'" not in script_path.read_text(encoding="utf-8-sig"):
            return False
    except OSError:
        return False
    launch_windows_update_script(script_path)
    return True


def self_test(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    cards = data.get("卡牌", [])
    required = {"编号", "名字", "基础信息", "地图", "挑战骰", "小卡", "人工校对", "牌面总结"}
    if len(cards) != 1713 or cards[0].get("编号") != "0002" or cards[-1].get("编号") != "1714":
        return 3
    if any(not required.issubset(card) for card in cards):
        return 4
    counts = {"大卡": 0, "小卡": 0, "交锋卡": 0}
    for card in cards:
        kind = card_kind(card)
        counts[kind] += 1
        summary = card.get("牌面总结", {})
        if int(summary.get("结构版本", 0) or 0) < SUMMARY_SCHEMA_VERSION or not str(summary.get("内容") or "").strip():
            return 5
    if counts != {"大卡": 801, "小卡": 900, "交锋卡": 12}:
        return 6
    return 0


def ui_self_test(path: Path) -> int:
    editor = VisualAuditEditor(path, test_mode=True)
    editor.update_idletasks()
    if editor.image_source is None:
        editor.destroy()
        return 5
    if not str(editor.review_summary.get("1.0", "end-1c")).strip():
        editor.destroy()
        return 8
    editor.toggle_image_focus()
    editor.update_idletasks()
    if not editor.image_focus_mode:
        editor.destroy()
        return 6
    editor.set_actual_size()
    editor.update_idletasks()
    if not 0.95 <= editor.display_scale <= 1.05:
        editor.destroy()
        return 7
    editor.destroy()
    return 0


def option_value(args: list[str], name: str) -> str:
    prefix = f"{name}="
    for index, arg in enumerate(args):
        if arg.startswith(prefix):
            return arg[len(prefix):].strip()
        if arg == name and index + 1 < len(args):
            return args[index + 1].strip()
    return ""


def main() -> int:
    if resume_pending_windows_update():
        return 0
    args = sys.argv[1:]
    if "--version" in args:
        print(f"v{EDITOR_VERSION}")
        return 0
    path_args: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--remote-config-url":
            skip_next = True
            continue
        if not arg.startswith("--"):
            path_args.append(arg)
    remote_url = option_value(args, "--remote-config-url") or os.environ.get(REMOTE_CONFIG_ENV, "").strip()
    if not remote_url and not path_args:
        remote_url = read_saved_remote_config_url()
    remote_sync: dict[str, Any] | None = None
    if remote_url and not path_args:
        path = REMOTE_CACHE_DIR / "manual_card_audit.remote.json"
        if not path.exists() and DEFAULT_JSON.exists():
            path = DEFAULT_JSON
        remote_sync = {"config_url": remote_url}
    else:
        path = Path(path_args[0]) if path_args else DEFAULT_JSON
    if not path.exists():
        return 2
    if "--ui-self-test" in args:
        return ui_self_test(path)
    if "--self-test" in args:
        return self_test(path)
    VisualAuditEditor(path, remote_sync).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
