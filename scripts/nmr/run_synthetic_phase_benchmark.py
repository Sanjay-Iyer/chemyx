"""Run independent synthetic cases in bounded worker processes; never hardware."""
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import _bootstrap
from chemyx_lab.analysis import synthetic_phase_benchmark as b,nmr_validation as v

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--generate',action='store_true')
    parser.add_argument('--mode',choices=['classical','deep'],default='classical');parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--limit',type=int);parser.add_argument('--start',type=int,default=0);args=parser.parse_args()
    if args.generate:b.make_cases();return
    cases=v.read_rows(b.BENCH/'synthetic_cases.csv')[args.start:]
    if args.limit:cases=cases[:args.limit]
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures={executor.submit(b.run_case,row,args.mode):row['case_id'] for row in cases}
        for i,future in enumerate(as_completed(futures),1):print(f'[{i}/{len(cases)}] {future.result()}',flush=True)

if __name__=='__main__':main()
