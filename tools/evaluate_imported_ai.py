"""Frozen imported AdaptiveAI vs this project's unchanged Normal policy."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from uno.ai import choose_action
from uno.engine import BY_ID

SOURCE = ROOT / 'build/external-ai-evaluation/source'
sys.path.insert(0, str(SOURCE))
from ai_learning import AdaptiveAI
from game import Game, COLORS, make_deck


def ordinary(game, rng):
    seat = game.current_index
    if game.phase == 'choose_color':
        legal = [{'type':'choose_color','color':c} for c in COLORS]
    elif game.phase == 'choose_target':
        legal = [{'type':'choose_player','target':i} for i in game.active if i != seat]
    elif game.phase == 'roulette_draw':
        legal = [{'type':'draw'}]
    else:
        legal = [{'type':'play','card_id':c.id} for c in game.legal_cards()] + [{'type':'draw'}]
    view = {'you':seat, 'legal':legal, 'hand':[{'color':c.color or 'wild'} for c in game.players[seat].hand]}
    return choose_action(view, rng, 'normal')



def devil_view(game):
    """Translate only public information into the unchanged Devil interface."""
    def card(c):
        identity = int(c.id.split(':')[-1]) + 1
        return BY_ID[identity].public()
    seat = game.current_index
    phase = {'playing':'turn', 'choose_target':'choose_player',
             'roulette_draw':'roulette', 'choose_color':'choose_color'}[game.phase]
    if phase == 'choose_color':
        legal = [{'type':'choose_color','color':c} for c in COLORS]
    elif phase == 'choose_player':
        legal = [{'type':'choose_player','target':i} for i in game.active if i != seat]
    elif phase == 'roulette':
        legal = [{'type':'draw'}]
    else:
        legal = [{'type':'play','card_id':card(c)['id']} for c in game.legal_cards()]
        legal += [{'type':'draw'}] if game.deck or len(game.discard)>1 else []
        legal = legal or [{'type':'end_turn'}]
    return {'you':seat,'current':seat,'phase':phase,'color':game.current_color,
            'direction':game.direction,'pending':game.draw_stack,'last_draw':game.last_draw,
            'penalty_started':False,'mercy_limit':36,'top':card(game.top_card),
            'hand':[card(c) for c in game.players[seat].hand],
            'players':[{'id':i,'name':p.name,'count':len(p.hand),'eliminated':p.eliminated}
                       for i,p in enumerate(game.players)],
            'known_discards':[card(c)['id'] for c in game.discard],
            'deck_count':len(game.deck),'discard_count':len(game.discard),
            'roulette_revealed':[card(c) for c in game.revealed_draws],
            'events':([{'type':'draw','revealed':True,'player':game.revealed_player}]
                      if game.revealed_draws and game.revealed_player is not None else []), 'legal':legal}


def devil(game, rng):
    action = choose_action(devil_view(game), rng, 'devil')
    if action['type'] == 'play':
        identity = action['card_id'] - 1
        action['card_id'] = next(c.id for c in game.players[game.current_index].hand
                                 if int(c.id.split(':')[-1]) == identity)
    return action

def play(seed, candidate_seat, opponent="normal"):
    agent = AdaptiveAI(SOURCE / 'ai_learning.json')
    initial = json.dumps(agent.data, sort_keys=True)
    game = Game(['P0','P1'], seed=seed)
    rngs = [random.Random(seed * 101 + seat * 997) for seat in range(2)]
    maximum = 0.0
    started = time.monotonic()
    for step in range(10000):
        if game.winner is not None:
            assert json.dumps(agent.data, sort_keys=True) == initial
            return {'seed':seed, 'seat':candidate_seat, 'won':game.winner==candidate_seat,
                    'actions':step, 'seconds':time.monotonic()-started, 'max_decision':maximum,
                    'backend':agent._gpu_reducer._status}
        actor = game.current_index
        before = {c.id for c in game.players[actor].hand}
        optional = False
        t = time.monotonic()
        if actor != candidate_seat:
            action = (ordinary if opponent == "normal" else devil)(game, rngs[actor])
        elif game.phase == 'choose_color':
            action = {'type':'choose_color','color':agent.choose_color(game, actor, COLORS)}
            agent.record_ai_color(actor, action['color'])
        elif game.phase == 'choose_target':
            targets = [i for i in game.active if i != actor]
            action = {'type':'choose_player','target':agent.choose_swap_target(game, actor, targets)}
        else:
            legal = game.legal_cards()
            if legal and agent.should_accept_penalty(game, actor, legal):
                action = {'type':'draw'}
            elif legal and agent.should_voluntarily_draw(game, actor, legal):
                action = {'type':'draw'}
                optional = True
                agent.record_ai_voluntary_draw(game, actor)
            elif legal:
                card = agent.choose_card(game, actor, legal)
                agent.record_ai_play(game, actor, card)
                action = {'type':'play','card_id':card.id}
            else:
                action = {'type':'draw'}
        if actor == candidate_seat:
            maximum = max(maximum, time.monotonic()-t)
        kind = action['type']
        if kind == 'play':
            card = next(c for c in game.players[actor].hand if c.id == action['card_id'])
            agent.observe_public_play(actor, card)
            zero = card.kind == 'number' and card.value == 0
            game.play(card.id)
            if zero and game.winner is None:
                agent.observe_zero_rotation(game, [candidate_seat])
        elif kind == 'choose_color':
            game.choose_color(action['color'])
        elif kind == 'choose_player':
            game.choose_target(action['target'])
            agent.observe_seven_swap(game, actor, action['target'], [candidate_seat])
        else:
            game.draw()
            if optional:
                if game.phase == 'playing' and game.current_index == actor:
                    agent.record_ai_draw_result(actor, [c for c in game.players[actor].hand if c.id not in before])
                else:
                    agent.clear_ai_pending_draw(actor)
        agent.prune_known_cards(game)
        cards = game.deck + game.discard + [c for p in game.players for c in p.hand]
        assert len(cards) == 168 and len({c.id for c in cards}) == 168
    raise RuntimeError(f'Unfinished round: {seed}, {candidate_seat}')


def pair(seed, opponent="normal"):
    return [play(seed, seat, opponent) for seat in (0,1)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--opponent', choices=('normal','devil'), default='normal')
    parser.add_argument('--seeds', type=int, default=200)
    parser.add_argument('--start-seed', type=int, default=30001)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    records = []
    jobs = range(args.start_seed, args.start_seed + args.seeds)
    if args.workers == 1:
        batches = (pair(seed, args.opponent) for seed in jobs)
        pool = None
    else:
        pool = ProcessPoolExecutor(max_workers=args.workers)
        futures = [pool.submit(pair, seed, args.opponent) for seed in jobs]
        batches = (f.result() for f in as_completed(futures))
    try:
        for batch in batches:
            records.extend(batch)
            args.output.with_suffix('.progress.json').write_text(json.dumps(records), encoding='utf-8')
            if len(records) % 20 == 0 or len(records) == args.seeds * 2:
                print(json.dumps({'rounds':len(records), 'wins':sum(r['won'] for r in records),
                                  'seconds':round(time.monotonic()-started,1)}), flush=True)
    finally:
        if pool:
            pool.shutdown(cancel_futures=True)
    records.sort(key=lambda r:(r['seed'],r['seat']))
    n = len(records); wins = sum(r['won'] for r in records); losses = n-wins
    rating = 1000 + 400 * math.log10((wins+0.5)/(losses+0.5))
    # Resample whole seed pairs: shared deals make two seats dependent.
    bootstrap = random.Random(96312)
    pair_wins = [int(records[i]['won'])+int(records[i+1]['won']) for i in range(0,n,2)]
    samples = sorted(1000+400*math.log10((w+0.5)/(n-w+0.5))
                     for w in (sum(bootstrap.choices(pair_wins,k=len(pair_wins))) for _ in range(10000)))
    report = {'reference':args.opponent, 'opponent':args.opponent, 'unit':'two-player round',
              'engine':'attached game.py (36 cards eliminate)', 'frozen_weights':True,
              'lookahead_samples':AdaptiveAI.LOOKAHEAD_SAMPLES, 'lookahead_depth':AdaptiveAI.LOOKAHEAD_DEPTH,
              'fast_mode':False, 'games':n, 'wins':wins, 'losses':losses, 'win_rate':wins/n,
              'elo':rating if args.opponent == 'normal' else None,
              'rating_difference':rating-1000,
              'paired_bootstrap_95_interval':[samples[249],samples[9749]] if args.opponent == 'normal' else None,
              'seat_wins':[sum(r['won'] for r in records if r['seat']==seat) for seat in (0,1)],
              'start_seed':args.start_seed, 'seed_count':args.seeds, 'seconds':time.monotonic()-started,
              'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE.iterdir() if p.is_file()},
              'devil_sha256':hashlib.sha256((ROOT/'uno/devil.py').read_bytes()).hexdigest(),
              'baseline_sha256':hashlib.sha256((ROOT/'uno/ai.py').read_bytes()).hexdigest(),
              'records':records}
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('records','source_sha256')}),flush=True)


if __name__ == '__main__':
    main()
