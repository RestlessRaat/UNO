"""Publish a measured Normal -> imported AI -> Devil calibration chain."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from uno.elo import BASE_ELO, CHAIN_METHOD, load_ai_elo, save_calibration


def validate(report):
    n=report['games']
    records=report['records']
    assert n == report['seed_count']*2 == len(records)
    assert len({(r['seed'],r['seat']) for r in records}) == n
    assert {(r['seed'],r['seat']) for r in records} == {(s,p) for s in range(report['start_seed'],report['start_seed']+report['seed_count']) for p in (0,1)}
    assert all(type(r['won']) is bool for r in records)
    assert sum(r['won'] for r in records) == report['wins']
    assert report['wins']+report['losses']==n
    assert report['frozen_weights'] and report['fast_mode'] is False
    assert report['unit']=='two-player round'


def difference(won,lost):
    return 400*math.log10((won+0.5)/(lost+0.5))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--duel',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'assets/ai_elo.json')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    base=json.loads(args.reference.read_text(encoding='utf-8'))
    duel=json.loads(args.duel.read_text(encoding='utf-8'))
    validate(base);validate(duel)
    assert base.get('opponent','normal')=='normal' and duel['opponent']=='devil'
    assert base['engine']==duel['engine']
    assert base['source_sha256']==duel['source_sha256']
    assert (base['lookahead_samples'],base['lookahead_depth'])==(duel['lookahead_samples'],duel['lookahead_depth'])
    assert duel['devil_sha256']==hashlib.sha256((ROOT/'uno/devil.py').read_bytes()).hexdigest()
    assert not ({r['seed'] for r in base['records']} & {r['seed'] for r in duel['records']})
    legs=[]
    for report,reference,challenger,wins in ((base,'normal','imported_ai',{'normal':base['losses'],'imported_ai':base['wins']}),
                  (duel,'imported_ai','devil',{'imported_ai':duel['wins'],'devil':duel['losses']})):
        legs.append({'reference':reference,'challenger':challenger,'games':report['games'],'wins':wins,
                     'start_seed':report['start_seed'],'seeds':report['seed_count'],'paired_seats':True,
                     'source_sha256':report['source_sha256'],'engine':report['engine']})
    anchor=BASE_ELO+difference(base['wins'],base['losses'])
    score=anchor+difference(duel['losses'],duel['wins'])
    groups=[]
    for report in (base,duel):
        records=sorted(report['records'],key=lambda r:(r['seed'],r['seat']))
        groups.append([int(records[i]['won'])+int(records[i+1]['won']) for i in range(0,len(records),2)])
    rng=random.Random(83011)
    samples=[]
    for _ in range(10000):
        a=sum(rng.choices(groups[0],k=len(groups[0])))
        b=sum(rng.choices(groups[1],k=len(groups[1])))
        samples.append(BASE_ELO+difference(a,base['games']-a)+difference(duel['games']-b,b))
    samples.sort()
    evidence={'version':1,'method':CHAIN_METHOD,'players':2,'unit':'round','reference':'normal',
              'reference_elo':BASE_ELO,'challenger':'devil','legs':legs,
              'ratings':{'normal':BASE_ELO,'imported_ai':anchor,'devil':score},
              'paired_bootstrap_95_interval':[samples[249],samples[9749]],
              'devil_sha256':duel['devil_sha256'],'baseline_sha256':duel['baseline_sha256'],
              'interpretation':'Indirect rating under attached 36-card engine; inherits reference uncertainty.'}
    if args.output.exists():
        old=json.loads(args.output.read_text(encoding='utf-8'))
        old_devil=old.get('comparisons',{}).get('devil')
        if old_devil and old_devil.get('method') != CHAIN_METHOD:
            evidence['superseded_calibration']=old_devil
    save_calibration(evidence,args.output)
    assert abs(load_ai_elo(args.output)['devil']-score)<1e-9
    args.report.write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps({'devil_wins':duel['losses'],'imported_wins':duel['wins'],'games':duel['games'],
                      'reference_elo':anchor,'devil_elo':score,'interval':evidence['paired_bootstrap_95_interval']}))


if __name__=='__main__':main()
