"""Extend, rather than rebuild, the retained 27-acquisition phase gallery."""
import argparse
from pathlib import Path
import _bootstrap
from chemyx_lab.analysis.additional_phase_methods import extend_gallery
from chemyx_lab.analysis.nmr_validation import DEFAULT_OUTPUT

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validation-dir',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--skip-deep',action='store_true',help='Explicitly omit DEEP inference; mark unavailable')
    args=parser.parse_args()
    extend_gallery(args.validation_dir,deep=not args.skip_deep)
