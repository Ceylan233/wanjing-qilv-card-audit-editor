#!/usr/bin/env python3
"""故事书 OCR 人工核验器。

这个窗口只编辑 story_review_entries_narrowed.json 的人工核验字段，保留原始 OCR，
避免人工修改覆盖证据文本。可以作为主卡牌编辑器的独立子窗口打开，也支持源码单独运行。
"""

from __future__ import annotations

import json
import os
import shutil
import tkinter as tk
import ssl
import threading
import urllib.request
from urllib.error import HTTPError
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any


BOOK_NAMES = {
    "move": "移动故事书",
    "look": "查看故事书",
    "engage": "接触故事书",
    "help": "帮助故事书",
    "take": "获取故事书",
    "overpower": "压制故事书",
    "depart": "离开故事书",
    "secrets": "秘密之书",
}
REASON_NAMES = {
    "action_result_separator_missing": "行动与结果没有明确分隔",
    "chinese_action_text_missing": "中文行动文本未识别完整",
    "official_branch_unresolved": "官方分支尚未对应",
}
STATUS_NAMES = {
    "needs_manual_review": "待人工核验",
    "confirmed": "已核验",
    "manual_revision": "已修订待复核",
}
STATUS_CODES = {value: key for key, value in STATUS_NAMES.items()}


def project_root() -> Path:
    start = Path(__file__).resolve().parents[1]
    if getattr(__import__("sys"), "frozen", False):
        start = Path(__import__("sys").executable).resolve().parent
    for root in (start, Path.cwd(), *start.parents):
        if (root / ".codex-temp/storybooks_ocr_zh/story_review_entries_narrowed.json").exists():
            return root
    return start


DEFAULT_PATH = project_root() / ".codex-temp/storybooks_ocr_zh/story_review_entries_narrowed.json"
REMOTE_CONFIG_URL = "https://syncinema.pw/wanjing-card-audit/config.json"
REMOTE_CACHE_DIR = Path.home() / ".wanjing-card-audit-editor"
REMOTE_URL_FILE = REMOTE_CACHE_DIR / "remote_config_url.txt"
REMOTE_CODE_FILE = REMOTE_CACHE_DIR / "security_code.txt"


def readable_reasons(values: Any) -> str:
    if not isinstance(values, list) or not values:
        return "无"
    return "；".join(REASON_NAMES.get(str(value), str(value)) for value in values)


def status_label(value: Any) -> str:
    return STATUS_NAMES.get(str(value), str(value) if value else "待人工核验")


def as_text(value: Any) -> str:
    return "" if value is None else str(value)


def read_saved_code() -> str:
    try:
        return REMOTE_CODE_FILE.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def save_code(value: str) -> None:
    if not value.strip():
        return
    REMOTE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    REMOTE_CODE_FILE.write_text(value.strip() + "\n", encoding="utf-8")
    try:
        REMOTE_CODE_FILE.chmod(0o600)
    except OSError:
        pass


def read_saved_url() -> str:
    try:
        return REMOTE_URL_FILE.read_text(encoding="utf-8").strip() or REMOTE_CONFIG_URL
    except (OSError, UnicodeError):
        return REMOTE_CONFIG_URL


def save_url(value: str) -> None:
    REMOTE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    REMOTE_URL_FILE.write_text(value.strip() + "\n", encoding="utf-8")


def ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def remote_get(url: str, code: str) -> tuple[dict[str, Any], str]:
    request = urllib.request.Request(url, headers={"X-Card-Audit-Code": code, "User-Agent": "wanjing-story-review-editor"})
    with urllib.request.urlopen(request, timeout=90, context=ssl_context()) as response:
        payload = json.loads(response.read().decode("utf-8-sig"))
        return payload, str(response.headers.get("ETag") or "")


def remote_binding(config_url: str, code: str) -> tuple[dict[str, Any], str]:
    config, _ = remote_get(config_url, code)
    nested = config.get("故事文本核验") if isinstance(config.get("故事文本核验"), dict) else {}
    document_url = str(nested.get("document_url") or "").strip()
    upload_url = str(nested.get("upload_url") or document_url).strip()
    if not document_url or not upload_url:
        raise RuntimeError("远程配置缺少“故事文本核验”文档 URL")
    binding = dict(nested)
    binding.update({"config_url": config_url, "document_url": document_url, "upload_url": upload_url})
    return binding, ""


class StoryReviewEditor(tk.Toplevel):
    """非模态新窗口；不会覆盖或锁住主卡牌编辑器。"""

    def __init__(self, master: tk.Misc | None = None, path: Path | None = None):
        if master is None:
            self._standalone_root = tk.Tk()
            super().__init__(self._standalone_root)
        else:
            self._standalone_root = None
            super().__init__(master)
        self.title("万境奇旅｜故事文本人工核验")
        self.geometry("1680x1020")
        self.minsize(1200, 760)
        self.path = Path(path or DEFAULT_PATH)
        self.document: dict[str, Any] = {}
        self.items: list[dict[str, Any]] = []
        self.current_index: int | None = None
        self.dirty = False
        self.loading = False
        self.search_var = tk.StringVar()
        self.book_var = tk.StringVar(value="全部故事书")
        self.status_filter_var = tk.StringVar(value="全部状态")
        self.status_var = tk.StringVar(value="正在加载…")
        self.entry_title_var = tk.StringVar(value="")
        self.book_label_var = tk.StringVar(value="")
        self.meta_var = tk.StringVar(value="")
        self.review_status_var = tk.StringVar(value="待人工核验")
        self.review_note_var = tk.StringVar(value="")
        self.checked_action_var = tk.BooleanVar()
        self.checked_result_var = tk.BooleanVar()
        self.checked_refs_var = tk.BooleanVar()
        self.uncertain_var = tk.BooleanVar()
        self._filtered_indices: list[int] = []
        self.remote_sync: dict[str, Any] | None = None
        self.remote_sync_running = False
        self.remote_etag = ""
        self._build()
        self.load_file(self.path)
        self.protocol("WM_DELETE_WINDOW", self.close_window)
        self.bind("<Control-s>", lambda _event: self.save_file())
        self.bind("<Control-Up>", lambda _event: self.select_relative(-1))
        self.bind("<Control-Down>", lambda _event: self.select_relative(1))
        if self._standalone_root is not None:
            self._standalone_root.withdraw()
            self.after_idle(self.deiconify)

    def _build(self) -> None:
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        toolbar = ttk.Frame(self, padding=8)
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, textvariable=self.status_var).grid(row=0, column=0, sticky="w", padx=(0, 18))
        ttk.Label(toolbar, text="搜索编号/正文").grid(row=0, column=1, sticky="e", padx=4)
        search = ttk.Entry(toolbar, textvariable=self.search_var, width=28)
        search.grid(row=0, column=2, sticky="w", padx=4)
        search.bind("<KeyRelease>", lambda _event: self.refresh_list())
        self.book_combo = ttk.Combobox(toolbar, textvariable=self.book_var, state="readonly", width=16)
        self.book_combo.grid(row=0, column=3, padx=4)
        self.book_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_list())
        self.status_combo = ttk.Combobox(toolbar, textvariable=self.status_filter_var, state="readonly", width=14,
                                         values=["全部状态", *STATUS_NAMES.values()])
        self.status_combo.grid(row=0, column=4, padx=4)
        self.status_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_list())
        ttk.Button(toolbar, text="保存", command=self.save_file).grid(row=0, column=5, padx=4)
        ttk.Button(toolbar, text="重新打开", command=self.reload_file).grid(row=0, column=6, padx=4)
        ttk.Button(toolbar, text="远程加载", command=self.load_remote).grid(row=0, column=7, padx=4)
        ttk.Button(toolbar, text="上传同步", command=self.sync_remote).grid(row=0, column=8, padx=4)
        ttk.Button(toolbar, text="上一个", command=lambda: self.select_relative(-1)).grid(row=0, column=9, padx=4)
        ttk.Button(toolbar, text="下一个", command=lambda: self.select_relative(1)).grid(row=0, column=10, padx=4)

        body = ttk.Panedwindow(self, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        left = ttk.Frame(body, padding=(0, 0, 8, 0))
        right = ttk.Frame(body, padding=(8, 0, 0, 0))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=3)
        right.rowconfigure(2, weight=2)
        right.columnconfigure(0, weight=1)
        body.add(left, weight=1)
        body.add(right, weight=4)

        columns = ("book", "entry", "status", "reason")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        for col, label, width in (("book", "故事书", 100), ("entry", "编号", 58), ("status", "状态", 100), ("reason", "需要核验", 260)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        summary = ttk.LabelFrame(left, text="当前列表说明", padding=8)
        summary.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        summary.columnconfigure(0, weight=1)
        ttk.Label(summary, text="这是 OCR/语义复核留下的 164 条候选，不是最终游戏文本。逐条确认后，右侧填写可读的中文版本。", wraplength=430, justify="left").grid(sticky="w")

        header = ttk.Frame(right)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, textvariable=self.book_label_var, font=("Microsoft YaHei UI", 12, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.entry_title_var, font=("Microsoft YaHei UI", 16, "bold")).grid(row=0, column=1, sticky="w", padx=18)
        ttk.Label(header, textvariable=self.meta_var).grid(row=0, column=2, sticky="e")

        text_pane = ttk.Panedwindow(right, orient="horizontal")
        text_pane.grid(row=1, column=0, sticky="nsew")
        ocr_frame = ttk.LabelFrame(text_pane, text="OCR 原文（只读证据）", padding=6)
        manual_frame = ttk.LabelFrame(text_pane, text="人工核验文本（可编辑）", padding=6)
        ocr_frame.rowconfigure(0, weight=1)
        ocr_frame.columnconfigure(0, weight=1)
        manual_frame.rowconfigure(0, weight=1)
        manual_frame.columnconfigure(0, weight=1)
        self.ocr_text = tk.Text(ocr_frame, wrap="word", font=("Microsoft YaHei UI", 12), state="disabled", padx=10, pady=8)
        self.manual_text = tk.Text(manual_frame, wrap="word", undo=True, maxundo=-1, font=("Microsoft YaHei UI", 12), padx=10, pady=8)
        self.ocr_text.grid(row=0, column=0, sticky="nsew")
        self.manual_text.grid(row=0, column=0, sticky="nsew")
        for frame, widget in ((ocr_frame, self.ocr_text), (manual_frame, self.manual_text)):
            sb = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
            widget.configure(yscrollcommand=sb.set)
            sb.grid(row=0, column=1, sticky="ns")
        self.manual_text.bind("<KeyRelease>", lambda _event: self.mark_dirty())
        text_pane.add(ocr_frame, weight=1)
        text_pane.add(manual_frame, weight=1)

        review = ttk.LabelFrame(right, text="人工核验字段", padding=8)
        review.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        review.columnconfigure(1, weight=1)
        review.rowconfigure(4, weight=1)
        ttk.Label(review, text="核验状态").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        status_box = ttk.Combobox(review, textvariable=self.review_status_var, values=list(STATUS_NAMES.values()), state="readonly", width=18)
        status_box.grid(row=0, column=1, sticky="w", padx=4, pady=4)
        status_box.bind("<<ComboboxSelected>>", lambda _event: self.mark_dirty())
        ttk.Label(review, text="问题解释").grid(row=1, column=0, sticky="nw", padx=4, pady=4)
        self.reason_label = ttk.Label(review, text="", wraplength=760, justify="left")
        self.reason_label.grid(row=1, column=1, sticky="w", padx=4, pady=4)
        checks = ttk.Frame(review)
        checks.grid(row=2, column=1, sticky="w", padx=4, pady=4)
        for label, variable in (("行动文字已核对", self.checked_action_var), ("结果分支已核对", self.checked_result_var), ("编号引用已核对", self.checked_refs_var), ("仍有不确定内容", self.uncertain_var)):
            ttk.Checkbutton(checks, text=label, variable=variable, command=self.mark_dirty).pack(side="left", padx=(0, 12))
        ttk.Label(review, text="人工备注").grid(row=3, column=0, sticky="nw", padx=4, pady=4)
        note = ttk.Entry(review, textvariable=self.review_note_var)
        note.grid(row=3, column=1, sticky="ew", padx=4, pady=4)
        note.bind("<KeyRelease>", lambda _event: self.mark_dirty())
        self.detail_text = tk.Text(review, height=8, wrap="word", font=("Microsoft YaHei UI", 10), state="disabled", padx=8, pady=6)
        self.detail_text.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=4, pady=4)
        detail_scroll = ttk.Scrollbar(review, orient="vertical", command=self.detail_text.yview)
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.grid(row=4, column=2, sticky="ns", pady=4)
        buttons = ttk.Frame(review)
        buttons.grid(row=5, column=0, columnspan=3, sticky="e", pady=(6, 0))
        ttk.Button(buttons, text="标记为已核验并保存", command=self.mark_confirmed).pack(side="right", padx=4)
        ttk.Button(buttons, text="保存当前条目", command=self.save_current).pack(side="right", padx=4)

    def load_file(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ValueError("文件缺少 items 数组")
        except Exception as exc:
            messagebox.showerror("打开故事核验文件失败", f"{path}\n\n{exc}", parent=self)
            return
        self.path = path
        self.document = data
        self.items = data["items"]
        self.dirty = False
        books = sorted({BOOK_NAMES.get(str(item.get("book")), str(item.get("book"))) for item in self.items})
        self.book_combo.configure(values=["全部故事书", *books])
        self.refresh_list(select_first=True)
        self.status_var.set(f"已加载 {len(self.items)} 条人工核验记录")

    def reload_file(self) -> None:
        if self.dirty and not messagebox.askyesno("放弃未保存修改？", "重新打开会丢弃当前未保存内容，是否继续？", parent=self):
            return
        self.load_file(self.path)

    def refresh_list(self, select_first: bool = False) -> None:
        query = self.search_var.get().strip().lower()
        book_filter = self.book_var.get()
        status_filter = self.status_filter_var.get()
        self.tree.delete(*self.tree.get_children())
        self._filtered_indices = []
        for index, item in enumerate(self.items):
            book = BOOK_NAMES.get(str(item.get("book")), str(item.get("book")))
            entry = as_text(item.get("entry_number"))
            status = status_label(item.get("status"))
            haystack = f"{book} {entry} {item.get('event_uid', '')} {item.get('recheck', {}).get('ocr_text', '')}".lower()
            if query and query not in haystack:
                continue
            if book_filter != "全部故事书" and book != book_filter:
                continue
            if status_filter != "全部状态" and status != status_filter:
                continue
            self._filtered_indices.append(index)
            self.tree.insert("", "end", iid=str(index), values=(book, entry, status, readable_reasons(item.get("remaining_reasons"))))
        if select_first and self._filtered_indices:
            self.tree.selection_set(str(self._filtered_indices[0]))
            self.tree.focus(str(self._filtered_indices[0]))
        elif self.current_index is not None and self.current_index in self._filtered_indices:
            self.tree.selection_set(str(self.current_index))

    def on_select(self, _event: tk.Event | None = None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        index = int(selected[0])
        if self.dirty and self.current_index is not None and index != self.current_index:
            answer = messagebox.askyesnocancel("当前条目未保存", "先保存当前条目再切换吗？", parent=self)
            if answer is None:
                self.tree.selection_set(str(self.current_index))
                return
            if answer:
                self.save_current()
            else:
                self.dirty = False
        self.show_item(index)

    def show_item(self, index: int) -> None:
        if index < 0 or index >= len(self.items):
            return
        self.loading = True
        self.current_index = index
        item = self.items[index]
        recheck = item.get("recheck") or {}
        book = BOOK_NAMES.get(str(item.get("book")), str(item.get("book")))
        entry = as_text(item.get("entry_number"))
        self.book_label_var.set(book)
        self.entry_title_var.set(f"条目 {entry}")
        self.meta_var.set(f"第 {as_text(recheck.get('page'))} 页 · 第 {as_text(recheck.get('column'))} 栏 · 置信度 {float(recheck.get('confidence') or 0):.3f}")
        self.set_readonly_text(self.ocr_text, as_text(recheck.get("ocr_text")))
        manual = item.get("人工核验") or item.get("manual_review") or {}
        manual_text = as_text(manual.get("文本") or manual.get("text") or recheck.get("ocr_text"))
        self.manual_text.delete("1.0", tk.END)
        self.manual_text.insert("1.0", manual_text)
        self.review_status_var.set(status_label(item.get("status")))
        self.review_note_var.set(as_text(manual.get("备注") or manual.get("note")))
        checked = manual.get("核验项") or manual.get("checked_fields") or {}
        self.checked_action_var.set(bool(checked.get("行动文字") or checked.get("action")))
        self.checked_result_var.set(bool(checked.get("结果分支") or checked.get("result")))
        self.checked_refs_var.set(bool(checked.get("编号引用") or checked.get("references")))
        self.uncertain_var.set(bool(checked.get("仍有不确定") or checked.get("uncertain")))
        self.reason_label.configure(text=readable_reasons(item.get("remaining_reasons")))
        details = [
            f"原始状态：{item.get('status', '')}",
            f"复核结论：{recheck.get('reason', '')}",
            f"独立 OCR 安全：{'是' if recheck.get('safe') else '否'}",
            f"行动/结果分隔：{'是' if recheck.get('has_separator') else '否'}",
            f"标题识别：{'是' if recheck.get('header_present') else '否'}",
            f"异常字符：{'有' if recheck.get('malformed_glyphs') else '无'}",
            f"预期引用编号：{', '.join(map(str, recheck.get('references_expected') or [])) or '无'}",
            f"OCR 识别编号：{', '.join(map(str, recheck.get('references_seen') or [])) or '无'}",
        ]
        self.set_readonly_text(self.detail_text, "\n".join(details))
        self.dirty = False
        self.loading = False

    @staticmethod
    def set_readonly_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def mark_dirty(self) -> None:
        if not self.loading:
            self.dirty = True
            self.status_var.set("当前条目有未保存修改")

    def collect_current(self) -> dict[str, Any] | None:
        if self.current_index is None:
            return None
        item = self.items[self.current_index]
        manual = item.setdefault("人工核验", {})
        manual.update({
            "文本": self.manual_text.get("1.0", "end-1c").strip(),
            "备注": self.review_note_var.get().strip(),
            "核验项": {
                "行动文字": bool(self.checked_action_var.get()),
                "结果分支": bool(self.checked_result_var.get()),
                "编号引用": bool(self.checked_refs_var.get()),
                "仍有不确定": bool(self.uncertain_var.get()),
            },
            "更新时间": datetime.now().isoformat(timespec="seconds"),
        })
        label = self.review_status_var.get()
        item["status"] = STATUS_CODES.get(label, "needs_manual_review")
        if item["status"] == "confirmed":
            item["remaining_reasons"] = []
        return item

    def save_current(self) -> None:
        if self.collect_current() is None:
            return
        self.dirty = False
        self.refresh_list()
        self.tree.selection_set(str(self.current_index))
        self.status_var.set(f"条目 {self.items[self.current_index].get('entry_number')} 已修改，尚未写入磁盘")

    def save_file(self) -> None:
        self.save_current()
        if not self.items:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            backup = self.path.with_suffix(self.path.suffix + ".bak")
            try:
                shutil.copy2(self.path, backup)
            except OSError:
                pass
        self.document["items"] = self.items
        self.document["count"] = len(self.items)
        self.document["last_saved_at"] = datetime.now().isoformat(timespec="seconds")
        self.path.write_text(json.dumps(self.document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.dirty = False
        self.status_var.set(f"已保存：{self.path.name}")

    def ask_remote_settings(self) -> tuple[str, str] | None:
        dialog = tk.Toplevel(self)
        dialog.title("配置故事文本远程同步")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=14)
        body.grid(sticky="nsew")
        url_var = tk.StringVar(value=read_saved_url())
        code_var = tk.StringVar(value=read_saved_code())
        ttk.Label(body, text="配置 JSON URL").grid(row=0, column=0, sticky="w", padx=4, pady=5)
        ttk.Entry(body, textvariable=url_var, width=72).grid(row=0, column=1, padx=4, pady=5)
        ttk.Label(body, text="安全码").grid(row=1, column=0, sticky="w", padx=4, pady=5)
        ttk.Entry(body, textvariable=code_var, show="*", width=72).grid(row=1, column=1, padx=4, pady=5)
        ttk.Label(body, text="故事文本会使用独立远程文件，不会覆盖卡牌校对 JSON。", foreground="#666666").grid(row=2, column=0, columnspan=2, sticky="w", padx=4, pady=5)
        result: list[tuple[str, str] | None] = [None]

        def accept() -> None:
            url, code = url_var.get().strip(), code_var.get().strip()
            if not url or not code:
                messagebox.showwarning("信息不完整", "请输入配置 URL 和安全码。", parent=dialog)
                return
            result[0] = (url, code)
            dialog.destroy()

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right", padx=4)
        ttk.Button(buttons, text="连接", command=accept).pack(side="right", padx=4)
        dialog.bind("<Return>", lambda _event: accept())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.wait_window()
        return result[0]

    def load_remote(self) -> None:
        if self.remote_sync_running:
            return
        settings = self.ask_remote_settings()
        if not settings:
            return
        config_url, code = settings
        self.status_var.set("正在加载远程故事核验文件…")
        self.remote_sync_running = True

        def worker() -> None:
            try:
                binding, _ = remote_binding(config_url, code)
                document, etag = remote_get(binding["document_url"], code)
                if not isinstance(document, dict) or not isinstance(document.get("items"), list):
                    raise RuntimeError("远程文件不是有效的故事核验 JSON")
                save_url(config_url)
                save_code(code)
                self.after(0, lambda: self.apply_remote_document(document, binding, etag))
            except HTTPError as exc:
                detail = "安全码不正确或服务器拒绝访问（HTTP 401）。" if exc.code == 401 else f"HTTP {exc.code}: {exc.reason}"
                self.after(0, lambda detail=detail: self.remote_error(detail))
            except Exception as exc:
                self.after(0, lambda detail=str(exc): self.remote_error(detail))

        threading.Thread(target=worker, name="story-review-remote-load", daemon=True).start()

    def apply_remote_document(self, document: dict[str, Any], binding: dict[str, Any], etag: str) -> None:
        self.remote_sync_running = False
        self.document = document
        self.items = list(document.get("items") or [])
        self.remote_sync = binding
        self.remote_etag = etag
        self.dirty = False
        self.refresh_list(select_first=True)
        self.status_var.set(f"已加载远程故事核验 {len(self.items)} 条")

    def sync_remote(self) -> None:
        if self.remote_sync_running:
            return
        if not self.remote_sync:
            self.load_remote()
            return
        self.save_current()
        self.document["items"] = self.items
        payload = (json.dumps(self.document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        code = read_saved_code()
        if not code:
            messagebox.showwarning("未配置安全码", "请先点击“远程加载”配置安全码。", parent=self)
            return
        self.remote_sync_running = True
        self.status_var.set("正在上传故事核验文件…")

        def worker() -> None:
            try:
                headers = {"X-Card-Audit-Code": code, "Content-Type": "application/json; charset=utf-8", "User-Agent": "wanjing-story-review-editor"}
                if self.remote_etag:
                    headers["If-Match"] = self.remote_etag
                request = urllib.request.Request(self.remote_sync["upload_url"], data=payload, headers=headers, method="PUT")
                with urllib.request.urlopen(request, timeout=120, context=ssl_context()) as response:
                    etag = str(response.headers.get("ETag") or self.remote_etag)
                self.after(0, lambda: self.remote_success(etag))
            except HTTPError as exc:
                detail = "远程文件已被其他设备修改，请先重新加载。" if exc.code == 412 else f"HTTP {exc.code}: {exc.reason}"
                self.after(0, lambda detail=detail: self.remote_error(detail))
            except Exception as exc:
                self.after(0, lambda detail=str(exc): self.remote_error(detail))

        threading.Thread(target=worker, name="story-review-remote-upload", daemon=True).start()

    def remote_success(self, etag: str) -> None:
        self.remote_sync_running = False
        self.remote_etag = etag
        self.status_var.set("故事核验文件已上传并同步")

    def remote_error(self, detail: str) -> None:
        self.remote_sync_running = False
        self.status_var.set("远程同步失败")
        messagebox.showerror("故事核验远程同步失败", detail, parent=self)

    def mark_confirmed(self) -> None:
        self.review_status_var.set("已核验")
        self.checked_action_var.set(True)
        self.checked_result_var.set(True)
        self.checked_refs_var.set(True)
        self.uncertain_var.set(False)
        self.save_file()

    def select_relative(self, delta: int) -> None:
        if not self._filtered_indices:
            return
        current_pos = self._filtered_indices.index(self.current_index) if self.current_index in self._filtered_indices else 0
        target = self._filtered_indices[(current_pos + delta) % len(self._filtered_indices)]
        self.tree.selection_set(str(target))
        self.tree.focus(str(target))
        self.tree.see(str(target))
        self.show_item(target)

    def close_window(self) -> None:
        if self.dirty and not messagebox.askyesno("未保存修改", "当前条目有未保存修改，仍要关闭窗口吗？", parent=self):
            return
        if self._standalone_root is not None:
            self._standalone_root.destroy()
        else:
            self.destroy()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="万境奇旅故事文本人工核验器")
    parser.add_argument("path", nargs="?", help="story_review_entries_narrowed.json 路径")
    args = parser.parse_args()
    path = Path(args.path) if args.path else DEFAULT_PATH
    if not path.exists():
        root = tk.Tk()
        root.withdraw()
        selected = filedialog.askopenfilename(title="选择故事核验 JSON", filetypes=[("JSON", "*.json"), ("全部文件", "*.*")])
        root.destroy()
        if not selected:
            return 2
        path = Path(selected)
    StoryReviewEditor(None, path).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
