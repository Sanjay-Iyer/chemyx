"""Precache independent later-cohort DEEP model runs; never touch old evidence."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import _bootstrap
from chemyx_lab.analysis import phase_gallery as g
from chemyx_lab.analysis import nmr_validation as v
from chemyx_lab.analysis import additional_phase_methods as a

def run(record):
    folder=v.DEFAULT_OUTPUT/'phase_validation'/record['acquisition_id']
    metadata=g.json_read(folder/'automated/processing_metadata.json')
    production=v.analyze(metadata['source_path'],v.production_args(metadata['source_path'],metadata['parameters']))
    status=a.run_deep(folder,production,record)
    return record['acquisition_id'],status['status']

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-index',type=int,required=True,help='Zero-based first acquisition, disjoint from currently running early sequence')
    parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--stop-index',type=int,default=None)
    args=parser.parse_args()
    records=g.discover_acquisitions()[args.start_index:args.stop_index]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(run,r) for r in records]):
            print(*future.result(),flush=True)
