"""Archive original procedure evidence and verify recorded Scratch geometry."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.inspect_sb3 import describe
from uno.animation import interpolate

PROCEDURES = ('intro.play', 'transition cards over', 'set transition target',
              'update cards for current index', 'jiggle', 'check for touching card',
              'pulse', 'wheel', 'Swap cards between players', 'rotate hands',
              'discard all card played', 'transition to bottom', 'tally points',
              'process colour select', 'transition to backs', '@events', '@clones')


def main():
    out = ROOT / 'build/baseline'
    out.mkdir(parents=True, exist_ok=True)
    text = io.StringIO()
    with redirect_stdout(text):
        for procedure in PROCEDURES:
            describe('uno', procedure)
    (out / 'scratch-animation-procedures.txt').write_text(text.getvalue(), encoding='utf8')
    trace = json.loads((out / 'original-animation-trace.json').read_text(encoding='utf8'))
    comparisons, maximum = 0, 0
    captured = set()
    for frame in trace:
        count = float(frame.get('Uno.Transition frames', 0))
        counter = float(frame.get('Uno.Transition counter', 0))
        if not 0 < counter < count or not frame.get('Uno.Card indexes.Transition cards'):
            continue
        for card in frame['Uno.Card indexes.Transition cards']:
            index = int(card) - 1
            def number(name):
                return float(frame['Uno.All Cards.' + name][index])
            start = (240 + number('Source.x'), 180 - number('Source.y'), 90 - number('Source.r'), 56.7, 88.55)
            end = (240 + number('Target.x'), 180 - number('Target.y'), 90 - number('Target.r'), 56.7, 88.55)
            expected = interpolate(start, end, counter / count)
            actual = (240 + number('Position.X'), 180 - number('Position.Y'), 90 - number('Direction'))
            error = max(abs(expected[0] - actual[0]), abs(expected[1] - actual[1]),
                        abs((expected[2] - actual[2] + 180) % 360 - 180))
            maximum = max(maximum, error)
            comparisons += 1
            captured.add(int(count))
    assert comparisons > 100, 'No usable source animation captured'
    assert maximum < 1e-8, f'Scratch geometry mismatch: {maximum}'
    report = {'recorded_frames': len(trace), 'card_frame_comparisons': comparisons,
              'captured_transition_frames': sorted(captured), 'maximum_coordinate_or_angle_error': maximum}
    (out / 'animation-audit.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
