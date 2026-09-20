import pytest
from uno.layout import hand_layout, hit_card, mouse_to_logical, viewport


@pytest.mark.parametrize("count", [1, 7, 24, 34])
def test_every_card_is_selectable(count):
    positions = hand_layout(0, 0, 4, count)
    for i, (x, y, _, w, h) in enumerate(positions):
        assert hit_card((x - w / 2 + 1, y), positions) == i


@pytest.mark.parametrize("size", [(960, 720), (1280, 720), (480, 360), (800, 1000)])
def test_mouse_scaling_preserves_coordinates(size):
    x, y, scale = viewport(size)
    assert mouse_to_logical((x + 199 * scale, y + 292 * scale), size) == pytest.approx((199, 292))
