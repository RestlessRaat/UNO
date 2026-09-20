"""480 x 360 Scratch coordinates, displayed on a crisp 2x canvas."""
import math


def viewport(size):
    w, h = size
    scale = min(w / 480, h / 360)
    return ((w - 480 * scale) / 2, (h - 360 * scale) / 2, scale)


def mouse_to_logical(pos, size):
    ox, oy, scale = viewport(size)
    return ((pos[0] - ox) / scale, (pos[1] - oy) / scale)


def seat_position(player, you, count):
    offset = (player - you) % count
    if offset == 0:
        return (199, 294, 0, 386)
    if count == 2:
        return (240, 64, 180, 400)
    if count == 3:
        return (110, 119, 230, 230) if offset == 1 else (370, 119, 130, 230)
    return {1: (65, 132, 270, 224), 2: (240, 64, 180, 250), 3: (415, 132, 90, 224)}[offset]


def hand_layout(player, you, count, cards):
    cx, cy, angle, span = seat_position(player, you, count)
    width, height = 56.7, 88.55
    spacing = min(width * 0.75, (span - width) / max(1, cards - 1))
    rad = math.radians(angle)
    result = []
    for i in range(cards):
        delta = (i - (cards - 1) / 2) * spacing
        result.append((cx + math.cos(rad) * delta, cy - math.sin(rad) * delta, angle, width, height))
    return result


def hit_card(pos, layout):
    """Local hand is unrotated; reversed search follows the visible z-order."""
    x, y = pos
    for i in range(len(layout) - 1, -1, -1):
        cx, cy, _, w, h = layout[i]
        if cx - w / 2 <= x < cx + w / 2 and cy - h / 2 <= y < cy + h / 2:
            return i
    return None
