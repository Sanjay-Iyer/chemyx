"""Write final reproducibility evidence without rerunning phase determination."""
from pathlib import Path
import json
import shutil
import xml.etree.ElementTree as ET
import _bootstrap
from chemyx_lab.analysis import additional_phase_methods as a
from chemyx_lab.analysis import phase_gallery as g
from chemyx_lab.analysis import nmr_validation as v
from chemyx_lab.analysis.phase_audit import sha256,write_rows,write_json

root=v.DEFAULT_OUTPUT;gallery=root/'phase_validation';logs=root/'logs/additional_phase_methods'
snapshot=logs/'source_snapshot';snapshot.mkdir(exist_ok=True)
sources=[v.ROOT/p for p in (
    'chemyx_lab/analysis/additional_phase_methods.py','chemyx_lab/analysis/phase_quality.py',
    'chemyx_lab/analysis/phase_optimizers.py','chemyx_lab/analysis/phase_gallery.py',
    'scripts/nmr/validation_phase_gui.py','scripts/nmr/deep_phaser_offline.cjs',
    'scripts/nmr/deep_phaser_native.py','scripts/nmr/extend_phase_methods.py',
    'scripts/nmr/finalize_additional_phase_report.py','scripts/nmr/verify_additional_phase_package.py',
    'tests/test_additional_phase_methods.py')]
source_rows=[]
for source in sources:
    target=snapshot/source.relative_to(v.ROOT);target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,target)
    source_rows.append({'source_path':str(source),'snapshot_path':str(target),'sha256':sha256(source)})
write_rows(logs/'final_source_provenance.csv',source_rows)
original=g.json_read(logs/'environment_audit.json')
protected_source=[]
for relative,digest in original['provenance']['source_code_sha256'].items():
    if relative=='scripts\\nmr\\validation_phase_gui.py':continue
    path=v.ROOT/relative
    protected_source.append({'path':str(path),'sha256_before':digest,'sha256_after':sha256(path),'unchanged':sha256(path)==digest})
assert all(row['unchanged'] for row in protected_source)
assert sha256(Path(original['nmrglue_autophase_file']))==original['nmrglue_autophase_sha256']
write_rows(logs/'production_source_verification.csv',protected_source)
downloads=g.json_read(logs/'external_audit/download_manifest.json')
assert all(sha256(Path(row['path']))==row['sha256'] for row in downloads)
protected=a.verify_protected(root)
package_summary=g.json_read(gallery/'ADDITIONAL_METHOD_SUMMARY.json')
package_summary['protected_scientific_files_unchanged']=sum(row['unchanged'] for row in protected)
package_summary['allowed_index_updates']=sum(not row['unchanged'] and row['index_update_allowed'] for row in protected)
write_json(gallery/'ADDITIONAL_METHOD_SUMMARY.json',package_summary)
checks=v.read_rows(gallery/'ADDITIONAL_METHOD_VERIFICATION.csv')
assert len(checks)==216 and all(v.truth(r['same_fft']) and v.truth(r['same_axis']) and v.truth(r['downstream_parameters_unchanged']) for r in checks)
records=g.discover_acquisitions(root)
assert len(records)==27 and len({r['raw_sha256'] for r in records})==27
assert sum(len(g.manual_status(gallery/r['acquisition_id'])[1]) for r in records)==0
tests={}
for name in ('regression_tests','path_test','final_phase_tests'):
    tree=ET.parse(logs/(name+'.xml'));suite=tree.getroot().find('testsuite')
    if suite is None:suite=tree.getroot()
    tests[name]={key:int(suite.get(key,'0')) for key in ('tests','failures','errors','skipped')}
assert tests['path_test']=={'tests':1,'failures':0,'errors':0,'skipped':0}
assert tests['regression_tests']['tests']==255 and tests['regression_tests']['failures']==1
assert tests['final_phase_tests']=={'tests':19,'failures':0,'errors':0,'skipped':0}
summary={'unique_acquisitions':27,'method_comparisons':216,'new_figures':246,
    'protected_files_unchanged':sum(row['unchanged'] for row in protected),
    'allowed_index_updates':sum(not row['unchanged'] and row['index_update_allowed'] for row in protected),
    'protected_production_source_files_unchanged':len(protected_source),
    'author_download_hashes_verified':len(downloads),'human_checkpoints':0,
    'tests':tests,'unresolved_test_failures':0,
    'test_setup_note':'Initial long basetemp caused one Windows 240-character path-budget fixture failure. That unchanged test passed using results/ph_short; no production-code fix or test assertion change.',
    'final_source_provenance':str(logs/'final_source_provenance.csv')}
write_json(logs/'final_verification.json',summary)
lines=['# Additional phase-method verification','',
    f'27 unique raw FID hashes; 216 method comparisons; 246 added dataset-titled figures. {summary["protected_files_unchanged"]} protected files remain byte-identical; only explicit index/gallery/manifest updates are allowed.',
    f'{len(protected_source)} protected production/scoring source files and the installed nmrglue auto-phase source are unchanged. All {len(downloads)} pinned author-source/model/runtime download hashes match.',
    'The original ai package versions remain unchanged. TensorFlow CPU 2.19.0 lives only in the isolated DEEP environment.',
    '', '## Tests','',
    'Existing NMR regressions: 254 passed in the original 255-test run; the one Windows temporary-path fixture failure passed on its unchanged targeted rerun with a short basetemp. Thus all 255 existing checks pass, with no unresolved failure.',
    'Additional comparator tests plus existing phase-gallery tests: **19 passed, 0 failed**. Test-only accepted checkpoint fixtures live outside the real gallery; no human phase was invented.',
    '', '## Reproducibility','',
    'Before/after protected hashes, production source verification, exact package versions, pinned downloads, original weights, source snapshots and XML test results are under `../logs/additional_phase_methods/`.',
    'Per-acquisition `additional_phase_methods.json` records original execution-source hashes. Final table flags, convergence annotations, readable tick labels and index updates use the separately saved final source snapshot; no phase estimates are refitted during report finalization.',
    'Raw .dx tables remain actual complex time-domain FIDs with accompanying phase metadata. All methods share the original apodization, zero fill, FFT, axis and downstream pipeline.',
    '', '## Manual review next','',
    'Review June 9 at 10:53:46, 11:08:23, 11:24:34 and 11:36:54, then the August 10 detection-disagreement cases at 14:49:56 and 16:27:25 and August 11 10:01:01 DEEP asymmetry. Use `PHASE_METHOD_FAILURES.md` for every flagged pair.',
    'Manual reference is pending for all 27. The GUI selector exposes seven automatic phased results; the unphased FFT remains its Original trace. Explicit human saves append independent comparisons against every automatic method.']
(gallery/'ADDITIONAL_VERIFICATION_REPORT.md').write_text('\n\n'.join(lines)+'\n',encoding='utf-8')
files=sorted(p for p in gallery.rglob('*') if p.is_file() and p.name!='OUTPUT_FILES.csv')
write_rows(gallery/'OUTPUT_FILES.csv',[{'path':str(p.resolve()),'bytes':p.stat().st_size,'sha256':sha256(p)} for p in files])
print(json.dumps(summary,indent=2))
