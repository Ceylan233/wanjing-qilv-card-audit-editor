#!/usr/bin/env python3
"""Generate concise, review-oriented summaries for every card category."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


SUMMARY_SCHEMA_VERSION = 4
AUTO_REVIEW_AUTHORITY_PREFIXES = ("fresh_ocr_and_visual_",)
SKIRM_CARD_NAMES = {
    1517: "电路·面具",
    1518: "风·号角",
    1519: "能量·护手",
    1520: "火·权杖",
    1521: "黄金·头盔",
    1522: "肌腱·甲胄",
    1523: "金属·斗篷",
    1524: "沙·典籍",
    1525: "石·十二面体",
    1526: "叶·披风",
    1527: "水·利刃",
    1528: "木·工具",
}
VERIFIED_CARD_TITLES = {
    987: "逆转沙漏",
    1385: "水诅咒",
    1386: "木诅咒",
    1387: "风诅咒",
    1388: "叶诅咒",
    1389: "石诅咒",
    1390: "沙诅咒",
    1391: "金属诅咒",
    1392: "肌腱诅咒",
    1393: "黄金诅咒",
    1394: "火诅咒",
    1395: "能量诅咒",
    1396: "电路诅咒",
    1707: "亲和之石",
}
GENERIC_NAMES = {
    "物品", "设备", "动物", "智慧生物", "智慧生物/NPC", "植物", "构筑物", "载具",
    "能力", "使命", "命运", "未命名", "",
}


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clipped(value: Any, limit: int = 220) -> str:
    text = compact_text(value)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def load_ability_index(path: Path) -> dict[int, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    grouped: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for ability in document.get("abilities", []):
        if isinstance(ability, dict):
            grouped[int(ability.get("card_id", 0))].append(ability)
    return dict(grouped)


def card_kind(card: dict[str, Any]) -> str:
    card_id = int(card.get("编号", 0) or 0)
    if card_id in SKIRM_CARD_NAMES:
        return "交锋卡"
    if bool(card.get("地图", {}).get("是否地点牌", False)):
        return "大卡"
    return "小卡"


def card_title(card: dict[str, Any]) -> str:
    card_id = int(card.get("编号", 0) or 0)
    if card_id in SKIRM_CARD_NAMES:
        return SKIRM_CARD_NAMES[card_id]
    if card_id in VERIFIED_CARD_TITLES:
        return VERIFIED_CARD_TITLES[card_id]
    name = card.get("名字", {})
    revised = compact_text(name.get("人工修订值"))
    detected = compact_text(name.get("当前中文名"))
    if revised:
        return revised
    if detected and detected not in GENERIC_NAMES:
        return detected
    return detected or "未命名"


def _cost_text(cost: Any) -> str:
    if not isinstance(cost, dict) or not cost:
        return ""
    labels = {
        "challenge_dice": "挑战骰",
        "card_reinforcement": "本卡强化",
        "boost": "强化",
        "coins": "钱币",
    }
    parts = [f"{labels.get(str(key), str(key))}{value}" for key, value in cost.items()]
    return "、".join(parts)


def _ability_lines(abilities: Iterable[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for ability in abilities:
        text = clipped(ability.get("text_zh"), 260)
        label = compact_text(ability.get("label_zh")) or "卡牌能力"
        cost = _cost_text(ability.get("cost"))
        status = compact_text(ability.get("execution_status"))
        if text:
            line = text
        else:
            line = label
        if cost and cost not in line:
            line = f"{label}（花费{cost}）：{line}"
        if status and status != "ready":
            line += f"〔{status}〕"
        if line not in lines:
            lines.append(line)
    return lines


def _review_guidance(
    card: dict[str, Any],
    kind: str,
    abilities: list[dict[str, Any]],
) -> dict[str, Any]:
    review_status = compact_text(card.get("人工校对", {}).get("总状态")) or "未校对"
    auto_abilities = [
        ability for ability in abilities
        if compact_text(ability.get("source_authority")).startswith(AUTO_REVIEW_AUTHORITY_PREFIXES)
    ]
    if review_status == "已核验":
        priority = "已核验"
        reason = "该卡已被人工标记为核验完成；再次修改结构化数据后仍应复查总结。"
    elif auto_abilities:
        priority = "优先"
        reason = f"{len(auto_abilities)}项运行能力主要由OCR与牌面视觉检测生成，建议对照最终中文卡图逐项确认。"
    else:
        priority = "常规"
        reason = "当前没有系统标记的高风险能力；仍需按最终中文卡图完成常规人工验收。"

    if kind == "大卡":
        checkpoints = [
            "地形与地区标记",
            "四向道路及移动花费",
            "每项行动的技能颜色、名称、花费、条件、收益与去向",
        ]
    elif kind == "交锋卡":
        checkpoints = [
            "速度值",
            "元素计分条件",
            "一次骰子能力的目标、数量、重掷/刷新方式与结算时机",
        ]
    else:
        checkpoints = [
            "卡牌类型、价值、放置强化与强化容量",
            "每个骰槽的颜色/技能、指定结果、强化花费/生成及闪电标志",
            "技能的发动时机、条件、花费、收益、目标卡号及储备/替换顺序",
        ]

    authorities: list[str] = []
    for ability in abilities:
        authority = compact_text(ability.get("source_authority")) or "未标明来源"
        if authority not in authorities:
            authorities.append(authority)
    return {
        "优先级": priority,
        "当前人工状态": review_status,
        "原因": reason,
        "核对重点": checkpoints,
        "能力来源": authorities,
        "OCR视觉生成能力数": len(auto_abilities),
    }


def _slot_line(slot: dict[str, Any], index: int) -> str:
    color = compact_text(slot.get("技能颜色", {}).get("中文"))
    family = compact_text(slot.get("技能家族"))
    skill = compact_text(slot.get("技能类型", {}).get("中文"))
    result = compact_text(slot.get("挑战骰结果要求", {}).get("中文"))
    slot_type = compact_text(slot.get("槽位类型", {}).get("中文")) or "骰槽"
    if skill and skill not in {"未识别", "未分类/不适用"}:
        identity = skill
    elif color and color != "无/未识别":
        identity = f"{color}{family or '技能'}"
    else:
        identity = family or slot_type
    parts = [f"骰槽{index}：{identity}"]
    if result and result not in {"任意结果/未指定", "未指定/未识别"}:
        parts.append(f"指定{result}结果")
    requirements = [
        compact_text(item.get("中文") if isinstance(item, dict) else item)
        for item in slot.get("挑战骰能力要求", [])
    ]
    requirements = [item for item in requirements if item]
    ability_labels = [
        compact_text(item)
        for item in re.split(r"[|｜]", compact_text(slot.get("能力文字")))
        if compact_text(item)
    ]
    if requirements and len(ability_labels) == len(requirements):
        paired_requirements = [
            f"{requirement}的“{label}”"
            for requirement, label in zip(requirements, ability_labels)
        ]
        parts.append("仅限" + "、".join(paired_requirements) + "行动")
    elif requirements:
        parts.append("需要" + "、".join(requirements))
    cost = int(slot.get("挑战骰强化花费", 0) or 0)
    reward = int(slot.get("挑战骰强化生成", 0) or 0)
    if cost:
        parts.append(f"花费{cost}点强化")
    if reward:
        parts.append(f"生成{reward}点强化")
    modifier = int(slot.get("额外投骰数量", 0) or 0)
    if modifier:
        parts.append(f"额外投{modifier}颗骰")
    return "；".join(parts) + "。"


def _effect_text(effect: Any) -> str:
    if not isinstance(effect, dict):
        return clipped(effect, 100)
    command = compact_text(effect.get("command") or effect.get("命令"))
    labels = {
        "gain_card": "获得卡牌",
        "move_player": "移动到地点",
        "adjust_stat": "调整资源",
        "gain_skill": "获得技能标记",
        "reserve_card": "储备卡牌",
        "replace_card": "替换卡牌",
        "choose": "进行选择",
        "resolve_skirm_card_rule": "结算交锋规则",
    }
    args: list[str] = []
    for key in ("card_id", "destination_card_id", "stat", "amount", "skill", "kind", "maximum"):
        if key in effect:
            args.append(f"{key}={effect[key]}")
    label = labels.get(command, command or "效果")
    return f"{label}（{'，'.join(args)}）" if args else label


def _action_line(action: dict[str, Any]) -> str:
    label = compact_text(action.get("行动文字")) or compact_text(action.get("分支标题")) or "地点行动"
    family = compact_text(action.get("行动家族"))
    event = action.get("对应地图事件") or {}
    cost = event.get("花费")
    if cost is None:
        cost = action.get("花费")
    result = clipped(event.get("结果文本"), 170)
    effects = [_effect_text(effect) for effect in event.get("结构化效果", [])]
    details = []
    if family:
        details.append(family)
    if cost is not None:
        details.append(f"花费{cost}")
    if result:
        details.append(result)
    elif effects:
        details.append("、".join(effects[:3]))
    return (f"{label}" + ("：" + "；".join(details) if details else "")).rstrip("。；;") + "。"


def _road_lines(card: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    roads = card.get("地图", {}).get("上下左右道路罗盘", {})
    for key, direction in (("上_北", "北"), ("右_东", "东"), ("下_南", "南"), ("左_西", "西")):
        road = roads.get(key, {})
        state = compact_text(road.get("道路状态"))
        target = compact_text(road.get("目标地点编号"))
        cost = road.get("移动花费")
        if target:
            detail = f"通往{target}"
            if cost is not None:
                detail += f"，花费{cost}"
            lines.append(f"{direction}{detail}")
        elif "星号" in state:
            lines.append(f"{direction}需查离开故事书")
    return lines


def _useful_card_text(card: dict[str, Any]) -> str:
    raw = (
        card.get("基础文本描述", {}).get("人工修订值")
        or card.get("基础文本描述", {}).get("当前中文描述")
        or ""
    )
    lines: list[str] = []
    for raw_line in str(raw).splitlines():
        line = compact_text(raw_line)
        if not line or re.fullmatch(r"[\dXW?※◆■□]+", line, flags=re.IGNORECASE):
            continue
        if line not in lines:
            lines.append(line)
    return clipped(" ".join(lines), 320)


def build_card_summary(
    card: dict[str, Any],
    abilities: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    abilities = list(abilities)
    number = str(card.get("编号", "")).zfill(4)
    kind = card_kind(card)
    title = card_title(card)
    elements: list[str] = []
    functions: list[str] = []
    base = card.get("基础信息", {})

    if kind == "大卡":
        map_data = card.get("地图", {})
        map_elements = map_data.get("地图元素", {})
        terrain = compact_text(map_elements.get("地形主类型"))
        tags = [compact_text(value) for value in map_elements.get("地形标签", []) if compact_text(value)]
        region = compact_text(map_elements.get("地区标记"))
        if terrain or tags:
            elements.append("地形：" + "、".join(dict.fromkeys([value for value in [terrain, *tags] if value])) + "。")
        if region:
            elements.append(f"地区标记：{region}。")
        roads = _road_lines(card)
        if roads:
            elements.append("道路罗盘：" + "；".join(roads) + "。")
        arrivals = map_data.get("地图事件", {}).get("抵达强制事件", [])
        if arrivals:
            elements.append(f"包含{len(arrivals)}项抵达强制事件。")
        actions = [*map_data.get("地点行动", []), *map_data.get("图画内地点行动", [])]
        if actions:
            functions.extend(_action_line(action) for action in actions[:8])
            if len(actions) > 8:
                functions.append(f"另有{len(actions) - 8}项地点行动，详见结构化数据。")
    elif kind == "交锋卡":
        speed_match = re.search(r"(?m)^\s*(\d{1,2})\s*$", str(card.get("基础文本描述", {}).get("当前中文描述") or ""))
        if speed_match:
            elements.append(f"速度值：{speed_match.group(1)}。")
        elements.append("专用于《交锋》对决；包含额外计分规则和一次骰子能力。")
        functions.extend(_ability_lines(abilities))
    else:
        small = card.get("小卡", {})
        subtype = compact_text(small.get("小卡类型", {}).get("中文"))
        if subtype and subtype != "未分类/不适用":
            elements.append(f"类型：{subtype}。")
        value = small.get("价值", {}).get("当前值")
        if value is None:
            value = base.get("价值", {}).get("当前值")
        if value is not None:
            elements.append(f"价值：{value}。")
        slots = card.get("挑战骰", {}).get("槽位", [])
        elements.extend(_slot_line(slot, index) for index, slot in enumerate(slots, 1))
        capacity = small.get("强化容量", {}).get("当前值")
        if capacity is not None:
            elements.append(f"强化容量：{capacity}格。")
        initial = small.get("放置时强化点数", {}).get("当前值")
        if initial:
            elements.append(f"放置时获得{initial}点强化。")
        functions.extend(_ability_lines(abilities))
        for value in small.get("人工可读放置效果修订", []):
            text = clipped(value, 240)
            if text and text not in functions:
                functions.append(text)

    if not elements:
        elements.append("当前结构化数据尚未识别出稳定的牌面组件。")
    if not functions:
        fallback = _useful_card_text(card)
        functions.append("牌面文字摘要：" + fallback if fallback else "当前尚无可靠的结构化功能描述。")

    guidance = _review_guidance(card, kind, abilities)

    # 大卡没有“小卡式牌名”。地点名称的历史识别值仍留在原始字段中供
    # 溯源，但不能作为大卡标题显示或导出。
    display_title = "" if kind == "大卡" else title
    heading = f"卡牌：{number}" if kind == "大卡" else f"卡牌：{number}「{display_title}」"
    content_lines = [
        heading,
        f"类别：{kind}",
        "牌面元素：",
        *[f"- {line}" for line in elements],
        "功能：",
        *[f"- {line}" for line in functions],
        "人工核验：",
        f"- 优先级：{guidance['优先级']}。{guidance['原因']}",
        "- 核对重点：" + "；".join(guidance["核对重点"]) + "。",
    ]
    return {
        "结构版本": SUMMARY_SCHEMA_VERSION,
        "卡牌类别": kind,
        "标题": display_title,
        "内容": "\n".join(content_lines),
        "生成来源": "卡牌结构化数据与已验证能力数据",
        "人工核验优先级": guidance["优先级"],
        "人工核验状态": guidance["当前人工状态"],
        "人工核验原因": guidance["原因"],
        "人工核验重点": guidance["核对重点"],
        "能力来源": guidance["能力来源"],
        "OCR视觉生成能力数": guidance["OCR视觉生成能力数"],
    }


def ensure_card_summary(
    card: dict[str, Any],
    abilities: Iterable[dict[str, Any]] = (),
    *,
    force: bool = False,
) -> bool:
    current = card.get("牌面总结")
    if not force and isinstance(current, dict) and int(current.get("结构版本", 0) or 0) >= SUMMARY_SCHEMA_VERSION and current.get("内容"):
        return False
    generated = build_card_summary(card, abilities)
    if current == generated:
        return False
    card["牌面总结"] = generated
    return True
