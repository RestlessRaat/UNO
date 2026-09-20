import json
import math
from pathlib import Path

import pytest

from uno.elo import BASE_ELO, DuelElo, load_ai_elo, save_calibration
from tools.calibrate_elo import calibrate


def test_unplayed_ratings_are_not_presented_as_measured():
    record = DuelElo()
    assert record.games == 0
    assert record.ratings() == {}
    assert record.report()["hard_win_rate"] is None


def test_equal_opponents_have_equal_elo():
    record = DuelElo(50, 50)
    assert record.ratings() == {"normal": BASE_ELO, "hard": BASE_ELO}


def test_elo_logistic_scale_and_small_sample_prior():
    record = DuelElo(100, 900)
    rating = record.ratings()
    expected = 1 / (1 + 10 ** ((rating["normal"] - rating["hard"]) / 400))
    assert expected == pytest.approx(900.5 / 1001)
    assert rating["hard"] - rating["normal"] == pytest.approx(381, abs=1)
    reversed_rating = DuelElo(900, 100).ratings()
    assert reversed_rating["hard"] - BASE_ELO == pytest.approx(-(rating["hard"] - BASE_ELO))
    assert math.isfinite(DuelElo(0, 100).ratings()["hard"])
    assert math.isfinite(DuelElo(100, 0).ratings()["hard"])


def test_recording_is_order_independent_and_counts_every_result():
    a, b = DuelElo(), DuelElo()
    winners = ["hard", "normal", "hard", "hard", "normal"]
    for winner in winners:
        a.record(winner)
    for winner in reversed(winners):
        b.record(winner)
    assert a.games == 5 and a.hard_wins == 3 and a.normal_wins == 2
    assert a.report() == b.report()
    with pytest.raises(ValueError):
        a.record("human")
    assert a.games == 5


@pytest.mark.parametrize("wins", [-1, 1.5, True, "3", None])
def test_invalid_counts_rejected(wins):
    with pytest.raises(ValueError):
        DuelElo(wins, 1)


def test_calibration_plays_swapped_seats_and_is_reproducible():
    report = calibrate(10, 3)
    assert report == calibrate(10, 3)
    assert report["games"] == 6 and sum(report["wins"].values()) == 6
    assert [seat["games"] for seat in report["hard_seat_results"]] == [3, 3]
    assert sum(seat["hard_wins"] for seat in report["hard_seat_results"]) == report["wins"]["hard"]
    assert report["paired_seats"] and report["actions"] > 0


@pytest.mark.parametrize("start,seeds", [(-1, 1), (1, 0), (65536, 1), (65535, 2)])
def test_calibration_rejects_invalid_seed_range(start, seeds):
    with pytest.raises(ValueError):
        calibrate(start, seeds)


def test_loading_recomputes_rating_from_recorded_results(monkeypatch):
    report = DuelElo(100, 300).report()
    report["ratings"]["hard"] = 9000  # Stale display values are not trusted.
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: json.dumps(report))
    assert load_ai_elo("irrelevant.json") == DuelElo(100, 300).ratings()


@pytest.mark.parametrize("report", [None, [], {}, {"version": 999},
                                    {**DuelElo(3, 4).report(), "games": 9},
                                    {**DuelElo(3, 4).report(), "players": 4}])
def test_invalid_rating_evidence_is_ignored(monkeypatch, report):
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: json.dumps(report))
    assert load_ai_elo("irrelevant.json") == {}


def test_missing_rating_file_is_optional(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(Path, "read_text", missing)
    assert load_ai_elo() == {}


def test_calibrations_share_the_same_anchor_and_preserve_each_other():
    path = Path(__file__).resolve().parents[1] / 'build/test-elo/multiple.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    hard = DuelElo(100, 300)
    devil = DuelElo(20, 180, 'devil')
    path.write_text(json.dumps(hard.report()), encoding='utf8')
    save_calibration(devil.report(), path)
    assert load_ai_elo(path) == {**hard.ratings(), **devil.ratings()}
    assert json.loads(path.read_text(encoding='utf8'))['version'] == 2
    with pytest.raises(ValueError):
        hard.record('devil')
    with pytest.raises(ValueError):
        devil.record('hard')
    assert hard.games == 400 and devil.games == 200


def test_devil_calibration_uses_real_devil_policy(monkeypatch):
    monkeypatch.setattr('uno.devil.ROLLOUT_SAMPLES', 1)
    report = calibrate(888, 1, challenger='devil')
    assert report['games'] == 2
    assert set(report['ratings']) == {'normal', 'devil'}
    assert set(report['wins']) == {'normal', 'devil'}
    assert 'devil.py' in report['source_sha256']



def chain_evidence():
    from uno.elo import CHAIN_METHOD
    return {"version":1,"method":CHAIN_METHOD,"players":2,"unit":"round",
            "reference":"normal","reference_elo":1000,"challenger":"devil",
            "legs":[{"reference":"normal","challenger":"imported_ai","games":400,
                     "wins":{"normal":76,"imported_ai":324}},
                    {"reference":"imported_ai","challenger":"devil","games":100,
                     "wins":{"imported_ai":40,"devil":60}}]}


def test_chain_recomputes_both_links_and_preserves_hard():
    path = Path(__file__).resolve().parents[1] / 'build/test-elo/chain.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DuelElo(100,300).report()),encoding='utf8')
    evidence = chain_evidence()
    evidence['ratings'] = {'devil':9999}
    save_calibration(evidence,path)
    ratings = load_ai_elo(path)
    assert ratings['devil'] == pytest.approx(1000+400*math.log10(324.5/76.5)+400*math.log10(60.5/40.5))
    assert ratings['hard'] == DuelElo(100,300).ratings()['hard']
    assert 'imported_ai' not in ratings


@pytest.mark.parametrize('change', ['anchor','count','negative','boolean','disconnected','endpoint','cycle'])
def test_invalid_chain_is_rejected(monkeypatch, change):
    report = chain_evidence()
    if change == 'anchor': report['reference_elo'] = 2000
    elif change == 'count': report['legs'][1]['games'] = 101
    elif change == 'negative': report['legs'][1]['wins']['devil'] = -1
    elif change == 'boolean': report['legs'][1]['wins']['devil'] = True
    elif change == 'disconnected': report['legs'][1]['reference'] = 'normal'
    elif change == 'endpoint': report['challenger'] = 'hard'
    else: report['legs'][1]['challenger'] = 'normal'
    monkeypatch.setattr(Path,'read_text',lambda *a,**kw:json.dumps(report))
    assert load_ai_elo('unused') == {}
