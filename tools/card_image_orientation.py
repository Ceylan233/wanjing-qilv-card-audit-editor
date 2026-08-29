"""Shared display-orientation rules for Chinese card scans."""

from __future__ import annotations

from typing import Any


DEFAULT_IMAGE_ROTATIONS = {
    **{str(number).zfill(4): 180 for number in range(1092, 1105)},
    **{str(number).zfill(4): 90 for number in range(1265, 1294)},
    "1138": 180,
    "1139": 180,
    "1146": 180,
    "1150": 90,
}


def card_display_rotation(card: dict[str, Any]) -> int:
    """Return the clockwise display correction without changing source pixels."""
    number = str(card.get("编号") or "").zfill(4)
    default_rotation = DEFAULT_IMAGE_ROTATIONS.get(number, 0)
    review = card.get("人工校对") or {}
    return int(review.get("图片显示旋转度数", default_rotation) or 0) % 360
