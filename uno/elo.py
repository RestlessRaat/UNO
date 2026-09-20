"""Relative AI Elo calibrated from paired, two-player round results.

Normal is the arbitrary 1000-point anchor. Fit the 400-point logistic Elo
curve to aggregate wins, rather than an order-sensitive last K-factor update.
A half-win/half-loss prior keeps even small or unanimous samples finite.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys

from .ai import DIFFICULTIES

BASE_ELO = 1000
METHOD = "paired-duel-elo-v1"
CHAIN_METHOD = "paired-duel-chain-v1"


@dataclass
class DuelElo:
    normal_wins: int = 0
    hard_wins: int = 0
    challenger: str = "hard"

    def __post_init__(self):
        if self.challenger not in ("hard", "devil"):
            raise ValueError("Unknown Elo challenger.")
        if any(type(value) is not int or value < 0 for value in (self.normal_wins, self.hard_wins)):
            raise ValueError("Win counts must be nonnegative integers.")

    @property
    def games(self):
        return self.normal_wins + self.hard_wins

    def record(self, winner):
        if winner not in ("normal", self.challenger):
            raise ValueError("Unknown AI difficulty.")
        if winner == "normal":
            self.normal_wins += 1
        else:
            self.hard_wins += 1

    def ratings(self):
        if not self.games:
            return {}
        difference = 400 * math.log10((self.hard_wins + 0.5) / (self.normal_wins + 0.5))
        return {"normal": float(BASE_ELO), self.challenger: BASE_ELO + difference}

    def report(self):
        return {"version": 1, "method": METHOD, "players": 2, "unit": "round",
                "reference": "normal", "reference_elo": BASE_ELO, "challenger": self.challenger,
                "games": self.games,
                "wins": {"normal": self.normal_wins, self.challenger: self.hard_wins},
                "ratings": self.ratings(),
                f"{self.challenger}_win_rate": self.hard_wins / self.games if self.games else None}


def _chain_ratings(report):
    """Recompute each measured link; never trust a supplied reference rating."""
    if (report["version"] != 1 or report["players"] != 2 or report["unit"] != "round"
            or report.get("reference") != "normal" or report.get("reference_elo") != BASE_ELO
            or report.get("challenger") not in ("hard", "devil")):
        return {}
    rating, previous = float(BASE_ELO), "normal"
    seen = {previous}
    legs = report["legs"]
    if not isinstance(legs, list) or len(legs) < 2:
        return {}
    for leg in legs:
        challenger = leg["challenger"]
        if not isinstance(challenger, str) or challenger in seen or leg["reference"] != previous:
            return {}
        wins = leg["wins"]
        if set(wins) != {previous, challenger}:
            return {}
        won, lost = wins[challenger], wins[previous]
        if any(type(n) is not int or n < 0 for n in (won, lost)):
            return {}
        if type(leg["games"]) is not int or won + lost != leg["games"] or not leg["games"]:
            return {}
        rating += 400 * math.log10((won + 0.5) / (lost + 0.5))
        seen.add(challenger)
        previous = challenger
    if previous != report["challenger"]:
        return {}
    return {"normal": float(BASE_ELO), previous: rating}


def _pair_ratings(report):
    if report.get("method") == CHAIN_METHOD:
        return _chain_ratings(report)
    if (report["version"] != 1 or report["method"] != METHOD
            or report["players"] != 2 or report["unit"] != "round"
            or report.get("reference", "normal") != "normal"
            or report.get("reference_elo", BASE_ELO) != BASE_ELO):
        return {}
    challenger = report.get("challenger", "hard")
    record = DuelElo(report["wins"]["normal"], report["wins"][challenger], challenger)
    return record.ratings() if report["games"] == record.games else {}


def load_ai_elo(path=None):
    """Load bundled evidence; absent/invalid evidence displays no invented rating."""
    if path is None:
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        path = root / "assets" / "ai_elo.json"
    try:
        report = json.loads(Path(path).read_text(encoding="utf8"))
        if report["version"] == 2 and report["method"] == METHOD:
            ratings = {}
            for challenger, evidence in report["comparisons"].items():
                if evidence.get("challenger", "hard") != challenger:
                    return {}
                ratings.update(_pair_ratings(evidence))
            return ratings
        return _pair_ratings(report)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def save_calibration(report, path):
    """Publish measured evidence without removing other difficulty comparisons."""
    if not _pair_ratings(report):
        raise ValueError("No valid completed calibration to publish.")
    path = Path(path)
    comparisons = {}
    if path.exists():
        old = json.loads(path.read_text(encoding="utf8"))
        if old.get("version") == 2 and old.get("method") == METHOD:
            comparisons.update(old["comparisons"])
        elif _pair_ratings(old):
            comparisons[old.get("challenger", "hard")] = old
    comparisons[report.get("challenger", "hard")] = report
    data = {"version": 2, "method": METHOD, "reference": "normal",
            "reference_elo": BASE_ELO, "comparisons": comparisons}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf8")
