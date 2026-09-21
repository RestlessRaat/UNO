from copy import deepcopy
import random

import pytest

from uno.animation import FPS, ANIMATION_SPEED, action_timeline, hover_layout, interpolate, opening
from uno.ai import choose_action
from uno.engine import BY_ID, GameConfig, new_game
from uno.layout import hand_layout


def test_opening_and_draw_run_at_one_and_a_half_speed():
    game = new_game(GameConfig(), 123)
    assert ANIMATION_SPEED == 1.5
    # Four-player opening was 267 source frames / 30 Hz = 8.9 seconds.
    assert opening(game.view_for(0)).duration == pytest.approx(8.9 / 1.5)
    before = game.view_for(0)
    game.apply_action({'type': 'draw', 'player_id': 0})
    timeline = action_timeline(before, game.view_for(0))
    assert timeline.duration == pytest.approx(0.5 / 1.5)
    assert timeline.sample(timeline.duration - 0.001)[3] is not None
    assert timeline.sample(timeline.duration)[3] is None


def test_voice_pause_preserves_speech_duration():
    game = new_game(GameConfig(), 123)
    old = game.view_for(0)
    new = deepcopy(old)
    new['events'] = [{'type': 'color', 'color': 'blue', 'player': 0}]
    timeline = action_timeline(old, new, lambda _: 1.2)
    assert 1.4 <= timeline.duration < 1.4 + 1 / FPS


def test_speeded_timeline_emits_sounds_once_at_the_correct_boundary():
    game = new_game(GameConfig(), 123)
    old = game.view_for(0)
    game.apply_action({'type': 'draw', 'player_id': 0})
    timeline = action_timeline(old, game.view_for(0))
    timeline.segments[0].end_sounds = ('test: end',)
    assert timeline.sounds_due(0) == ['sfx: draw slow']
    assert timeline.sounds_due(timeline.duration - 0.001) == []
    assert timeline.sounds_due(timeline.duration) == ['test: end']
    assert timeline.sounds_due(timeline.duration + 1) == []


def test_opening_is_round_robin_and_first_discard_is_separate():
    game = new_game(GameConfig(), 123)
    timeline = opening(game.view_for(0))
    deals = [s for s in timeline.segments if s.kind == 'deal']
    assert len(deals) == 28
    assert [s.target_owner for s in deals] == list(range(4)) * 7
    assert all(s.frames == 6 for s in deals)
    first = next(s for s in timeline.segments if s.kind == 'first_discard')
    assert first.before.top is None and first.after.top == game.view_for(0)['top']
    assert len(first.before.hands[0]) == 7
    assert first.frames == 6 and timeline.final.deck_count == 139


def test_draw_reflows_whole_hand_and_fades_back():
    game = new_game(GameConfig(), 123)
    old = game.view_for(0)
    game.apply_action({'type': 'draw', 'player_id': 0})
    timeline = action_timeline(old, game.view_for(0))
    segment = timeline.segments[0]
    assert segment.frames == 15
    _, poses, progress = segment.sample(6 / FPS)
    assert progress == pytest.approx(6 / 15)
    start = hand_layout(0, 0, 4, 7)[0]
    end = hand_layout(0, 0, 4, 8)[0]
    assert poses[0][1] == pytest.approx(interpolate(start, end, progress))
    face, position, back_alpha = poses[-1]
    assert face == game.view_for(0)['hand'][-1]
    assert 0 < back_alpha < 255
    assert len(segment.before.hands[0]) == 7 and len(segment.after.hands[0]) == 8


def test_transfer_samples_continuously_between_source_frames():
    game = new_game(GameConfig(), 123)
    old = game.view_for(0)
    game.apply_action({'type': 'draw', 'player_id': 0})
    segment = action_timeline(old, game.view_for(0)).segments[0]
    start = hand_layout(0, 0, 4, 7)[0]
    end = hand_layout(0, 0, 4, 8)[0]

    _, at_start, start_progress = segment.sample(0)
    _, at_frame, frame_progress = segment.sample(6 / FPS)
    _, between, between_progress = segment.sample(6.5 / FPS)
    _, at_end, end_progress = segment.sample(segment.frames / FPS)

    assert start_progress == 0
    assert at_start[0][1] == pytest.approx(start)
    assert frame_progress == pytest.approx(6 / 15)
    assert between_progress == pytest.approx(6.5 / 15)
    assert between[0][1] == pytest.approx(interpolate(start, end, between_progress))
    assert between[0][1] != pytest.approx(at_frame[0][1])
    assert end_progress == 1
    assert at_end[0][1] == pytest.approx(end)


def test_swap_moves_every_card_in_sequence_not_three_fake_backs():
    game = new_game(GameConfig(), 42)
    game.phase = 'choose_player'
    old = game.view_for(0)
    game.apply_action({'type': 'choose_player', 'target': 1, 'player_id': 0})
    new = game.view_for(0)
    timeline = action_timeline(old, new, lambda _: 0)
    slides = [s for s in timeline.segments if s.kind == 'transfer']
    assert len(slides) == 14
    assert [s.source_owner for s in slides] == [1] * 7 + [0] * 7
    assert all(s.frames == 6 for s in slides)
    assert len(slides[6].after.hands[0]) == 14
    assert len(slides[-1].after.hands[0]) == 7
    assert timeline.final.visible_view()['hand'] == new['hand']


def test_hover_jiggles_without_scaling_and_expands_neighbours():
    layout = hand_layout(0, 0, 4, 34)
    result = hover_layout(layout, 12, 0.37)
    assert result[12][3:] == layout[12][3:]
    assert abs(result[12][0] - layout[12][0]) <= 4
    assert abs(result[12][1] - layout[12][1]) <= 8
    assert abs(result[12][2]) <= 6
    assert result[13][0] - result[12][0] > 34
    assert result[:12] == layout[:12]


def test_every_action_of_full_round_can_be_presented_without_mutating_rules():
    game = new_game(GameConfig(), 7)
    rng = random.Random(7)
    seen = set()
    for _ in range(3000):
        if game.winner is not None:
            break
        old = game.view_for(0)
        game.apply_action(choose_action(game.view_for(game.current), rng))
        new = game.view_for(0)
        unchanged = deepcopy(game.replay())
        timeline = action_timeline(old, new, lambda _: 0)
        for s in timeline.segments:
            seen.add(s.kind)
            for t in (0, s.frames / FPS / 2, (s.frames - 1) / FPS):
                s.sample(t)
        if new['winner'] is None:
            assert timeline.final.visible_view()['hand'] == new['hand']
        assert game.replay() == unchanged
        assert game.view_for(0) == new
    assert game.winner is not None
    assert {'play', 'draw', 'transfer', 'mercy', 'pulse'} <= seen


def test_scoring_collects_and_reveals_each_opponent_card():
    game = new_game(GameConfig(('A', 'B', 'C')), 123)
    old = game.view_for(0)
    game._finish(0)
    game.revision += 1
    game.last_events = game.events
    new = game.view_for(0)
    timeline = action_timeline(old, new, lambda _: 0)
    tallies = [s for s in timeline.segments if s.kind == 'tally']
    assert len(tallies) == 14 and all(s.frames == 10 for s in tallies)
    assert all(s.moving.card for s in tallies)
    assert timeline.final.hands == [[], [], []]
    assert timeline.final.deck_count == 168 and timeline.final.top is None
    assert timeline.final.view['round_points'] == game.round_points


def test_hidden_cards_remain_hidden_during_remote_draw():
    game = new_game(GameConfig(), 123)
    game.current = 1
    old = game.view_for(0)
    game.apply_action({'type': 'draw', 'player_id': 1})
    segment = action_timeline(old, game.view_for(0)).segments[0]
    assert segment.sample(0.2)[1][-1][0] is None


@pytest.mark.parametrize('overrun', [0, 0.001, 1])
def test_completed_draw_still_renders_hands_without_revealing_opponents(overrun):
    game = new_game(GameConfig(), 123)
    old = game.view_for(0)
    game.apply_action({'type': 'draw', 'player_id': 0})
    timeline = action_timeline(old, game.view_for(0))
    scene, cards, progress, segment = timeline.sample(timeline.duration + overrun)
    assert len(cards) == sum(map(len, game.hands)) == 29
    assert [card for card, _, _ in cards if card] == game.view_for(0)['hand']
    assert sum(card is None for card, _, _ in cards) == 21
    assert scene.top == game.view_for(0)['top']
    assert progress == 1 and segment is None
