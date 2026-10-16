"""HOME-only adversarial validation; never call a live instrument entry point."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import _bootstrap  # noqa: F401
from chemyx_lab.testing.false_completion_validation import observation_campaign, workflow_campaign, spectral_campaign, raw_counterexample, guard


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--mode",choices=("observations","workflows","spectra","raw-counterexample"),required=True)
    p.add_argument("--histories",type=int,default=2000)
    args=p.parse_args()
    with ExitStack() as stack:
        guard(stack)
        if args.mode=="observations":observation_campaign(args.output,histories=args.histories)
        elif args.mode=="workflows":workflow_campaign(args.output)
        elif args.mode=="spectra":spectral_campaign(args.output)
        else:raw_counterexample(args.output)


if __name__=="__main__":main()
