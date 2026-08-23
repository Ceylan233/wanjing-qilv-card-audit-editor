#!/usr/bin/env python3
"""Minimal card-prompt editor and Codex batch task exporter.

This editor intentionally exposes only card number, prompt status, and one
prompt box. It is a planning surface for Codex; it does not silently mutate
runtime code by itself.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

try:
    from editor_build_version import EDITOR_VERSION
except ImportError:
    EDITOR_VERSION = "0.3.39"

MAP_ACTION_ROWS = [
    ("move", "蓝色", "移动"),
    ("look", "紫色", "查看"),
    ("engage", "橙色", "接触"),
    ("help", "绿色", "帮助"),
    ("take", "黄色", "获取"),
    ("overpower", "红色", "压制"),
]
MAP_ACTION_EFFECTS = ["", "受到1点伤害", "受到1点缺氧伤害", "受到1点高温伤害", "失去1点时间", "失去1点士气", "获得1点强化", "获得1点随机技能", "退出", "继续"]
MAP_ACTION_ELEMENTS = ["", "智慧生物", "动物", "元素体", "植物", "构筑物", "宝箱", "飞船残骸", "符文投射器"]

def find_project() -> Path:
    """Find a local project beside the EXE or current working directory."""
    starts = [Path.cwd()]
    if getattr(sys, "frozen", False):
        starts.insert(0, Path(sys.executable).resolve().parent)
    else:
        starts.insert(0, Path(__file__).resolve().parents[1])
    for start in starts:
        for candidate in (start, *start.parents):
            if (candidate / "data" / "rules" / "zh_cn" / "manual_card_audit.json").is_file():
                return candidate
    return starts[0]


PROJECT = find_project()
DEFAULT_JSON = PROJECT / "data" / "rules" / "zh_cn" / "manual_card_audit.json"


def load_document(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or not isinstance(document.get("卡牌"), list):
        raise ValueError("文件缺少“卡牌”数组")
    return document


def card_number(card: dict) -> str:
    return str(card.get("编号", "")).zfill(4)


def prompt_value(card: dict) -> str:
    return str((card.get("人工校对") or {}).get("待AI处理提示词") or "").strip()


def prompt_status(card: dict) -> str:
    review = card.get("人工校对") or {}
    prompt = prompt_value(card)
    if not prompt:
        return "未填写"
    if str(review.get("AI处理状态") or "") == "已完成":
        return "已处理"
    return "已给出提示词"


def apply_prompt(card: dict, prompt: str, now: str | None = None) -> bool:
    prompt = prompt.strip()
    review = card.setdefault("人工校对", {})
    previous = prompt_value(card)
    changed = previous != prompt
    if prompt:
        review["待AI处理提示词"] = prompt
        if changed or not review.get("AI处理状态"):
            review["AI处理状态"] = "待处理"
            review["提示词最后修改时间"] = now or datetime.now().astimezone().isoformat(timespec="seconds")
            review.pop("AI处理结果摘要", None)
            review.pop("AI处理完成时间", None)
    else:
        review.pop("待AI处理提示词", None)
        review.pop("AI处理状态", None)
        review.pop("提示词最后修改时间", None)
        review.pop("AI处理结果摘要", None)
        review.pop("AI处理完成时间", None)
    return changed


def build_codex_task_package(document: dict, source_path: Path) -> dict:
    cards = []
    for card in document.get("卡牌", []):
        prompt = prompt_value(card)
        if not prompt:
            continue
        cards.append({
            "编号": card_number(card),
            "提示词状态": prompt_status(card),
            "提示词": prompt,
            "卡牌数据": deepcopy(card),
        })
    return {
        "协议版本": 1,
        "用途": "将人工卡牌提示词交给 Codex，逐张人工核对卡图并创建或修改对应游戏代码。",
        "来源文件": str(source_path),
        "导出时间": datetime.now().astimezone().isoformat(timespec="seconds"),
        "待处理数量": len(cards),
        "Codex执行指令": [
            "逐张处理任务，不得把一张卡的结论套用到其它卡。",
            "每张卡以该卡的提示词为最高优先级，并核对最终中文卡图。",
            "修改人工校对总表、runtime_cards、card_abilities及其生成器或权威覆盖源。",
            "涉及规则时修改对应游戏运行代码，加入或更新针对该卡的回归测试。",
            "代码完成后运行相关测试；在任务中记录修改文件、测试结果和未解决问题。",
            "不要只改牌面总结文字；结构化数据和实际运行逻辑必须同步。",
        ],
        "卡牌": cards,
    }


def export_codex_tasks(document: dict, source_path: Path, target: Path) -> int:
    package = build_codex_task_package(document, source_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return int(package["待处理数量"])


class CardPromptEditor(tk.Tk):
    def __init__(self, path: Path):
        super().__init__()
        self.title(f"万境奇旅｜卡牌提示词编辑器 v{EDITOR_VERSION}")
        self.geometry("1380x820")
        self.minsize(1040, 620)
        self.path = path
        self.document = load_document(path)
        self.cards = sorted(self.document["卡牌"], key=lambda card: int(card.get("编号", 0) or 0))
        self.by_number = {card_number(card): card for card in self.cards}
        self.current_number: str | None = None
        self.image_source: Image.Image | None = None
        self.image_tk: ImageTk.PhotoImage | None = None
        self.dirty = False
        self.map_config_dirty = False
        self.search_var = tk.StringVar()
        self.filter_var = tk.StringVar(value="全部")
        self.map_interaction_filter = tk.StringVar(value="全部")
        self.visible_card_numbers: list[str] = []
        self.current_status = tk.StringVar(value="未选择")
        self.status_var = tk.StringVar(value=f"已加载 {len(self.cards)} 张卡")
        self._build()
        self.populate_list()
        if self.cards:
            self.card_list.selection_set(0)
            self.on_select()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build(self) -> None:
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=0, minsize=250)
        self.columnconfigure(1, weight=3, minsize=420)
        self.columnconfigure(2, weight=2, minsize=360)
        toolbar = ttk.Frame(self, padding=8)
        toolbar.grid(row=0, column=0, columnspan=3, sticky="ew")
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(toolbar, text="保存", command=self.save).grid(row=0, column=1, padx=4)
        ttk.Button(toolbar, text="导出 Codex 任务包", command=self.export_tasks).grid(row=0, column=2, padx=4)
        ttk.Button(toolbar, text="导出全部提示词", command=self.export_all).grid(row=0, column=3, padx=4)

        left = ttk.Frame(self, padding=(8, 0, 5, 8))
        left.grid(row=1, column=0, sticky="nsew")
        left.rowconfigure(3, weight=1)
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text="卡牌编号").grid(row=0, column=0, sticky="w")
        search = ttk.Entry(left, textvariable=self.search_var)
        search.grid(row=1, column=0, sticky="ew", pady=(3, 5))
        search.bind("<KeyRelease>", lambda _event: self.populate_list())
        self.filter_box = ttk.Combobox(left, textvariable=self.filter_var, state="readonly", values=["全部", "未填写", "已给出提示词", "已处理"])
        self.filter_box.grid(row=1, column=1, sticky="ew", padx=(5, 0), pady=(3, 5))
        self.filter_box.bind("<<ComboboxSelected>>", lambda _event: self.populate_list())
        left.columnconfigure(1, weight=0)
        self.map_filter_box = ttk.Combobox(left, textvariable=self.map_interaction_filter, state="readonly", values=["全部", "有地图互动", "无地图互动"])
        self.map_filter_box.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        self.map_filter_box.bind("<<ComboboxSelected>>", lambda _event: self.populate_list())
        list_frame = ttk.Frame(left)
        list_frame.grid(row=3, column=0, columnspan=2, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        self.card_list = tk.Listbox(list_frame, exportselection=False, font=("Consolas", 11))
        self.card_list.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.card_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.card_list.configure(yscrollcommand=scroll.set)
        self.card_list.bind("<<ListboxSelect>>", self.on_select)

        image_frame = ttk.Frame(self, padding=(5, 0, 5, 8))
        image_frame.grid(row=1, column=1, sticky="nsew")
        image_frame.rowconfigure(0, weight=1)
        image_frame.columnconfigure(0, weight=1)
        self.image_canvas = tk.Canvas(image_frame, bg="#202124", highlightthickness=0)
        self.image_canvas.grid(row=0, column=0, sticky="nsew")
        self.image_canvas.bind("<Configure>", lambda _event: self.refresh_image())

        right = ttk.Frame(self, padding=(5, 0, 8, 8))
        right.grid(row=1, column=2, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        ttk.Label(right, textvariable=self.current_status, font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        self.editor_tabs = ttk.Notebook(right)
        self.editor_tabs.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self._build_prompt_tab()
        self._build_map_action_tab()
        buttons = ttk.Frame(right)
        buttons.grid(row=2, column=0, sticky="e", pady=(6, 0))
        ttk.Button(buttons, text="上一张", command=lambda: self.move_selection(-1)).pack(side="left", padx=3)
        ttk.Button(buttons, text="下一张", command=lambda: self.move_selection(1)).pack(side="left", padx=3)
        ttk.Button(buttons, text="保存当前提示词", command=self.save_current).pack(side="left", padx=3)
        self.bind("<Control-s>", lambda _event: self.save())

    def _build_prompt_tab(self) -> None:
        tab = ttk.Frame(self.editor_tabs, padding=6)
        self.editor_tabs.add(tab, text="提示词")
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)
        ttk.Label(tab, text="给 Codex 的事实纠正提示词；留空表示该卡还没有提示词。", foreground="#6b4b00", wraplength=420).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.prompt_text = tk.Text(tab, wrap="word", undo=True, font=("Segoe UI", 12), bg="#fff8dc")
        self.prompt_text.grid(row=1, column=0, sticky="nsew")

    def _build_map_action_tab(self) -> None:
        outer = ttk.Frame(self.editor_tabs)
        self.editor_tabs.add(outer, text="地图行动")
        self.map_action_page = outer
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        canvas = tk.Canvas(outer, highlightthickness=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scroll.set)
        tab = ttk.Frame(canvas, padding=6)
        tab.columnconfigure(1, weight=1)
        window = canvas.create_window((0, 0), window=tab, anchor="nw")
        tab.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        ttk.Label(tab, text="地点卡没有挑战骰槽。地图背景互动只用于筛选；六色地图行动会自动生成提示词。", foreground="#174f2a", wraplength=420, justify="left").grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        background = ttk.LabelFrame(tab, text="地图背景互动（不属于地图行动）", padding=4)
        background.grid(row=1, column=0, columnspan=2, sticky="ew", pady=3)
        self.map_background_vars: dict[str, tk.BooleanVar] = {}
        for index, (code, color, label) in enumerate(MAP_ACTION_ROWS):
            variable = tk.BooleanVar(value=False)
            variable.trace_add("write", self.note_map_config_edit)
            self.map_background_vars[code] = variable
            ttk.Checkbutton(background, text=f"{color}{label}", variable=variable).grid(row=index // 2, column=index % 2, sticky="w", padx=4)
        card_effect = ttk.LabelFrame(tab, text="卡牌内容（不属于任何地图行动）", padding=4)
        card_effect.grid(row=2, column=0, columnspan=2, sticky="ew", pady=3)
        card_effect.columnconfigure(1, weight=1)
        self.card_dialogue = tk.BooleanVar(value=False)
        self.card_forced_effect = tk.StringVar()
        self.card_element = tk.StringVar()
        self.card_always_available = tk.StringVar()
        self.card_dialogue.trace_add("write", self.note_map_config_edit)
        self.card_forced_effect.trace_add("write", self.note_map_config_edit)
        self.card_element.trace_add("write", self.note_map_config_edit)
        self.card_always_available.trace_add("write", self.note_map_config_edit)
        ttk.Checkbutton(card_effect, text="有对话", variable=self.card_dialogue).grid(row=0, column=0, columnspan=2, sticky="w", padx=3)
        ttk.Label(card_effect, text="强制效果").grid(row=1, column=0, sticky="w", padx=3)
        ttk.Combobox(card_effect, textvariable=self.card_forced_effect, values=MAP_ACTION_EFFECTS, state="normal").grid(row=1, column=1, sticky="ew", padx=3)
        ttk.Label(card_effect, text="元素").grid(row=2, column=0, sticky="w", padx=3)
        ttk.Combobox(card_effect, textvariable=self.card_element, values=MAP_ACTION_ELEMENTS, state="normal").grid(row=2, column=1, sticky="ew", padx=3)
        ttk.Label(card_effect, text="始终可用（手填）").grid(row=3, column=0, sticky="w", padx=3)
        ttk.Entry(card_effect, textvariable=self.card_always_available).grid(row=3, column=1, sticky="ew", padx=3)
        ttk.Label(tab, text="六色地图行动", font=("Segoe UI", 10, "bold")).grid(row=3, column=0, columnspan=2, sticky="w", pady=(7, 2))
        self.map_action_vars: dict[str, dict[str, tk.Variable]] = {}
        for row, (code, color, label) in enumerate(MAP_ACTION_ROWS, start=4):
            box = ttk.LabelFrame(tab, text=f"{color}{label}", padding=4)
            box.grid(row=row, column=0, columnspan=2, sticky="ew", pady=3)
            box.columnconfigure(1, weight=1)
            values: dict[str, tk.Variable] = {
                "enabled": tk.BooleanVar(value=False), "note": tk.StringVar(),
            }
            self.map_action_vars[code] = values
            for variable in values.values():
                variable.trace_add("write", self.note_map_config_edit)
            ttk.Checkbutton(box, text="作为地图行动", variable=values["enabled"]).grid(row=0, column=0, sticky="w", padx=3)
            ttk.Label(box, text="备注").grid(row=1, column=0, sticky="w", padx=3)
            ttk.Entry(box, textvariable=values["note"]).grid(row=1, column=1, sticky="ew", padx=3)
        ttk.Button(tab, text="根据选择生成提示词", command=self.generate_map_prompt).grid(row=10, column=1, sticky="e", pady=8)

    def note_map_config_edit(self, *_args) -> None:
        if self.current_number:
            self.map_config_dirty = True

    def map_interaction_codes(self, card: dict) -> set[str]:
        if not card.get("地图", {}).get("是否地点牌", False):
            return set()
        review = card.get("人工校对", {})
        stored = review.get("地图行动配置", {}).get("地图背景互动", {})
        if isinstance(stored, dict) and stored:
            return {code for code, enabled in stored.items() if enabled}
        codes = set()
        for section in ("地点行动", "图画内地点行动"):
            for action in card.get("地图", {}).get(section, []):
                code = str((action.get("故事书") or {}).get("原值") or "")
                if code in {item[0] for item in MAP_ACTION_ROWS}:
                    codes.add(code)
        return codes

    def load_map_config(self, card: dict) -> None:
        config = card.get("人工校对", {}).get("地图行动配置", {})
        actions = config.get("地图行动", {}) if isinstance(config, dict) else {}
        content = config.get("卡牌内容", {}) if isinstance(config, dict) else {}
        # 兼容刚才尚未保存的早期结构：将行内字段提升为卡牌字段。
        legacy = next((item for item in actions.values() if isinstance(item, dict) and any(item.get(key) for key in ("对话", "强制效果", "元素"))), {})
        legacy_always = "、".join(f"{color}{label}" for code, color, label in MAP_ACTION_ROWS if bool((actions.get(code) or {}).get("始终可用")))
        self.card_dialogue.set(bool(content.get("有对话", content.get("对话") or legacy.get("对话"))))
        self.card_forced_effect.set(str(content.get("强制效果") or legacy.get("强制效果") or ""))
        self.card_element.set(str(content.get("元素") or legacy.get("元素") or ""))
        self.card_always_available.set(str(content.get("始终可用") or legacy_always or ""))
        background = self.map_interaction_codes(card)
        for code, _color, _label in MAP_ACTION_ROWS:
            self.map_background_vars[code].set(code in background)
            item = actions.get(code, {}) if isinstance(actions, dict) else {}
            values = self.map_action_vars[code]
            values["enabled"].set(bool(item.get("启用", False)))
            values["note"].set(str(item.get("备注") or ""))
        is_location = bool(card.get("地图", {}).get("是否地点牌", False))
        self.editor_tabs.tab(self.map_action_page, state="normal" if is_location else "hidden")
        if not is_location:
            self.editor_tabs.select(0)
        self.map_config_dirty = False

    def collect_map_config(self) -> dict:
        actions = {}
        for code, _color, _label in MAP_ACTION_ROWS:
            values = self.map_action_vars[code]
            actions[code] = {
                "启用": bool(values["enabled"].get()),
                "备注": str(values["note"].get()).strip(),
            }
        return {
            "版本": 1,
            "地图背景互动": {code: bool(variable.get()) for code, variable in self.map_background_vars.items()},
            "卡牌内容": {
                "有对话": bool(self.card_dialogue.get()),
                "强制效果": self.card_forced_effect.get().strip(),
                "元素": self.card_element.get().strip(),
                "始终可用": self.card_always_available.get().strip(),
            },
            "地图行动": actions,
        }

    def build_map_prompt(self, card: dict, config: dict) -> str:
        background = config["地图背景互动"]
        content = config["卡牌内容"]
        actions = config["地图行动"]
        interaction_labels = [f"{color}{label}" for code, color, label in MAP_ACTION_ROWS if background.get(code)]
        lines = [f"卡牌 {card_number(card)} 是地点卡，不含挑战骰槽。"]
        lines.append("地图背景互动（仅用于筛选，不属于地图行动）：" + ("、".join(interaction_labels) if interaction_labels else "无") + "。")
        card_details = []
        if content["有对话"]:
            card_details.append("包含对话")
        if content["强制效果"]:
            card_details.append(f"强制效果：{content['强制效果']}")
        if content["元素"]:
            card_details.append(f"元素：{content['元素']}")
        if content["始终可用"]:
            card_details.append(f"始终可用：{content['始终可用']}")
        lines.append("卡牌内容：" + ("；".join(card_details) if card_details else "无。"))
        selected = []
        for code, color, label in MAP_ACTION_ROWS:
            item = actions[code]
            if not item["启用"]:
                continue
            details = []
            if item["备注"]:
                details.append(f"备注：{item['备注']}")
            selected.append(f"{color}{label}地图行动：" + "；".join(details) + "。" if details else f"{color}{label}地图行动。")
        lines.append("地图行动：" + ("\n".join(selected) if selected else "无。"))
        lines.append("请只更新地点卡的地图行动和对应运行逻辑；不要创建或修改挑战骰槽。")
        return "\n".join(lines)

    def generate_map_prompt(self) -> None:
        if not self.current_number:
            return
        card = self.by_number[self.current_number]
        if not card.get("地图", {}).get("是否地点牌", False):
            return
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", self.build_map_prompt(card, self.collect_map_config()))
        self.map_config_dirty = True
        self.status_var.set("已按六色地图行动生成提示词；保存或切换卡牌时写入")

    def visible_numbers(self) -> list[str]:
        query = self.search_var.get().strip().lower()
        wanted = self.filter_var.get()
        wanted_map_interaction = self.map_interaction_filter.get()
        result = []
        for card in self.cards:
            number = card_number(card)
            status = prompt_status(card)
            if query and query not in number.lower():
                continue
            if wanted != "全部" and status != wanted:
                continue
            has_map_interaction = bool(self.map_interaction_codes(card))
            if wanted_map_interaction == "有地图互动" and not has_map_interaction:
                continue
            if wanted_map_interaction == "无地图互动" and has_map_interaction:
                continue
            result.append(number)
        return result

    def row_text(self, card: dict) -> str:
        return f"{card_number(card)}    {prompt_status(card)}"

    def populate_list(self) -> None:
        selected = self.current_number
        self.visible_card_numbers = self.visible_numbers()
        self.card_list.delete(0, tk.END)
        for number in self.visible_card_numbers:
            self.card_list.insert(tk.END, self.row_text(self.by_number[number]))
        if selected in self.visible_card_numbers:
            index = self.visible_card_numbers.index(selected)
            self.card_list.selection_set(index)
            self.card_list.see(index)

    def on_select(self, _event=None) -> None:
        selected = self.card_list.curselection()
        if not selected:
            return
        index = int(selected[0])
        if index >= len(self.visible_card_numbers):
            return
        number = self.visible_card_numbers[index]
        if self.current_number and number != self.current_number:
            self.save_current(refresh_list=False)
        self.current_number = number
        card = self.by_number[number]
        text = prompt_value(card)
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", text)
        self.current_status.set(f"卡牌 {number}｜{prompt_status(card)}")
        self.load_image(card)
        self.load_map_config(card)
        self.populate_list()

    def resolve_image(self, card: dict) -> Path | None:
        candidates = [
            card.get("基础信息", {}).get("中文源图片"),
            card.get("原始结构化卡牌数据", {}).get("source_image"),
        ]
        texture = card.get("基础信息", {}).get("贴图资源")
        if texture and str(texture).startswith("res://"):
            candidates.append(PROJECT / str(texture)[6:])
        folder = "大卡" if card.get("地图", {}).get("是否地点牌") else "标准卡"
        candidates.extend([
            PROJECT / "正式素材" / folder / f"{card_number(card)}.jpg",
            self.path.parent / "正式素材" / folder / f"{card_number(card)}.jpg",
        ])
        for value in candidates:
            if value:
                path = Path(value).expanduser()
                if path.is_file():
                    return path
        return None

    def load_image(self, card: dict) -> None:
        path = self.resolve_image(card)
        self.image_source = None
        if path:
            try:
                image = Image.open(path).convert("RGB")
                rotation = int((card.get("人工校对") or {}).get("图片显示旋转度数", 0) or 0) % 360
                self.image_source = image.rotate(-rotation, expand=True) if rotation else image
            except OSError:
                self.image_source = None
        self.refresh_image()

    def refresh_image(self) -> None:
        if not hasattr(self, "image_canvas"):
            return
        self.image_canvas.delete("all")
        width = max(200, self.image_canvas.winfo_width() - 20)
        height = max(200, self.image_canvas.winfo_height() - 20)
        if self.image_source is None:
            self.image_canvas.create_text(width / 2, height / 2, text="找不到中文卡图", fill="white", font=("Segoe UI", 13))
            return
        scale = min(width / self.image_source.width, height / self.image_source.height)
        resized = self.image_source.resize(
            (max(1, int(self.image_source.width * scale)), max(1, int(self.image_source.height * scale))),
            Image.Resampling.LANCZOS,
        )
        self.image_tk = ImageTk.PhotoImage(resized)
        self.image_canvas.create_image(width / 2, height / 2, image=self.image_tk, anchor="center")

    def save_current(self, refresh_list: bool = True) -> None:
        if not self.current_number:
            return
        card = self.by_number[self.current_number]
        config_changed = False
        if self.map_config_dirty and card.get("地图", {}).get("是否地点牌", False):
            config = self.collect_map_config()
            card.setdefault("人工校对", {})["地图行动配置"] = config
            self.prompt_text.delete("1.0", tk.END)
            self.prompt_text.insert("1.0", self.build_map_prompt(card, config))
            config_changed = True
        changed = apply_prompt(card, self.prompt_text.get("1.0", "end-1c"))
        self.dirty = self.dirty or changed or config_changed
        self.map_config_dirty = False
        self.current_status.set(f"卡牌 {self.current_number}｜{prompt_status(card)}")
        if refresh_list:
            self.populate_list()
        self.status_var.set(f"当前卡牌 {self.current_number} 的提示词已暂存" if changed else f"当前卡牌 {self.current_number} 没有变化")

    def save(self) -> None:
        self.save_current()
        if not self.dirty:
            self.status_var.set("没有需要保存的修改")
            return
        self.path.write_text(json.dumps(self.document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.dirty = False
        self.status_var.set(f"已保存：{self.path.name}")

    def export_tasks(self) -> None:
        self.save_current()
        target = filedialog.asksaveasfilename(title="导出 Codex 任务包", initialdir=str(self.path.parent), initialfile="card_prompt_codex_tasks.json", defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not target:
            return
        count = export_codex_tasks(self.document, self.path, Path(target))
        self.status_var.set(f"已导出 {count} 张卡的 Codex 任务包")
        messagebox.showinfo("导出完成", f"已导出 {count} 张卡。\n\n{target}")

    def export_all(self) -> None:
        self.save_current()
        target = filedialog.asksaveasfilename(title="导出全部提示词", initialdir=str(self.path.parent), initialfile="card_prompts.json", defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not target:
            return
        payload = {"协议版本": 1, "总数": len(self.cards), "卡牌": [{"编号": card_number(card), "提示词状态": prompt_status(card), "提示词": prompt_value(card)} for card in self.cards]}
        Path(target).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.status_var.set(f"已导出全部 {len(self.cards)} 张卡的提示词")

    def move_selection(self, delta: int) -> None:
        numbers = self.visible_numbers()
        if not numbers:
            return
        current = numbers.index(self.current_number) if self.current_number in numbers else 0
        target = max(0, min(len(numbers) - 1, current + delta))
        self.card_list.selection_clear(0, tk.END)
        self.card_list.selection_set(target)
        self.card_list.see(target)
        self.on_select()

    def on_close(self) -> None:
        if self.dirty:
            answer = messagebox.askyesnocancel("有未保存修改", "是否保存提示词后退出？")
            if answer is None:
                return
            if answer:
                self.save()
        self.destroy()


def ui_self_test(path: Path) -> int:
    editor = CardPromptEditor(path)
    editor.update_idletasks()
    if editor.image_source is None or editor.current_number != "0002":
        editor.destroy()
        return 5
    editor.map_action_vars["move"]["enabled"].set(True)
    editor.card_always_available.set("蓝色移动")
    editor.card_forced_effect.set("受到1点缺氧伤害")
    editor.generate_map_prompt()
    if "地点卡，不含挑战骰槽" not in editor.prompt_text.get("1.0", "end-1c"):
        editor.destroy()
        return 6
    if len(editor.visible_card_numbers) < 2:
        editor.destroy()
        return 7
    editor.card_list.selection_clear(0, tk.END)
    editor.card_list.selection_set(1)
    editor.on_select()
    if editor.current_number != editor.visible_card_numbers[1]:
        editor.destroy()
        return 8
    small_number = next((number for number, card in editor.by_number.items() if not card.get("地图", {}).get("是否地点牌", False)), "")
    if not small_number:
        editor.destroy()
        return 9
    editor.card_list.selection_clear(0, tk.END)
    editor.card_list.selection_set(editor.visible_card_numbers.index(small_number))
    editor.on_select()
    if editor.editor_tabs.tab(editor.map_action_page, "state") != "hidden":
        editor.destroy()
        return 10
    result = 0
    editor.destroy()
    return result


def self_test(path: Path) -> int:
    document = load_document(path)
    cards = document["卡牌"]
    if len(cards) != 1713:
        return 2
    numbers = [card_number(card) for card in cards]
    if len(set(numbers)) != len(numbers) or numbers[0] != "0002" or numbers[-1] != "1714":
        return 3
    package = build_codex_task_package(document, path)
    if package["待处理数量"] != sum(bool(prompt_value(card)) for card in cards):
        return 4
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--version", action="version", version=f"v{EDITOR_VERSION}")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--ui-self-test", action="store_true")
    parser.add_argument("--export-tasks", type=Path)
    args = parser.parse_args()
    if args.self_test:
        try:
            return self_test(args.path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"自检失败：{exc}")
            return 2
    if args.ui_self_test:
        try:
            return ui_self_test(args.path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"界面自检失败：{exc}")
            return 2
    if args.export_tasks:
        document = load_document(args.path)
        print(export_codex_tasks(document, args.path, args.export_tasks))
        return 0
    path = args.path
    if not path.is_file():
        picker = tk.Tk()
        picker.withdraw()
        selected = filedialog.askopenfilename(
            title="选择卡牌校对 JSON",
            initialdir=str(PROJECT),
            filetypes=[("卡牌校对 JSON", "*.json"), ("所有文件", "*.*")],
        )
        picker.destroy()
        if not selected:
            return 1
        path = Path(selected)
    try:
        CardPromptEditor(path).mainloop()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        messagebox.showerror("无法打开卡牌文件", str(exc))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
