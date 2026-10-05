"""Collect the final audit record from current evidence files; every claim is computed, none hard-coded.

v1 of this script and its outputs are archived in logs/superseded_v1_20261004/.
"""
from pathlib import Path
import csv, json, hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE.parents[1]


def js(p): return json.loads((BASE/p).read_text(encoding='utf-8-sig'))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def junit(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == 'testsuite' else list(root)
    count = lambda k: sum(int(s.get(k, 0)) for s in suites)
    return {'file': str(path), 'tests': count('tests'), 'failures': count('failures'), 'errors': count('errors'), 'skipped': count('skipped')}


integrity = js('verification/FINAL_INTEGRITY_CHECK.json')
assert integrity['passed'], 'integrity check did not pass'
tests = [junit(p) for p in sorted((BASE/'verification').glob('junit_*.xml'))]
assert tests and all(t['failures'] == t['errors'] == 0 for t in tests), tests
with (BASE/'logs/figure_manifest.csv').open(encoding='utf-8-sig', newline='') as f:
    figures = list(csv.DictReader(f))
dataset = figures[0]['dataset_display_name']
bad = [r['path'] for r in figures if sha(r['path']) != r['sha256'] or not r['visible_title'].startswith(dataset+' ')
       or r['visible_title'].count(dataset) != 1]
assert not bad, bad
notes = sorted((BASE/'verification').glob('[0-9][0-9]_*_review.md'))
verdicts = {}
for n in notes:
    text = n.read_text(encoding='utf-8')
    lines_ = [x.strip() for x in text.splitlines() if x.strip().startswith('VERDICT:')]
    verdicts[n.name] = lines_[-1].split(':', 1)[1].strip() if lines_ else 'v1 stage note (see file)'
# Refinement stages 1-7 are notes 08-14; the audit passes only when every one exists and its last verdict is APPROVED (fail closed).
STAGE_NOTES = range(8, 15)
present = {int(n.name[:2]) for n in notes if int(n.name[:2]) in STAGE_NOTES}
pending = [k for k in STAGE_NOTES if k not in present]
not_approved = [n.name for n in notes if int(n.name[:2]) in STAGE_NOTES and not verdicts[n.name].upper().startswith('APPROVED')]
reviews_passed = not pending and not not_approved
deck = js('boss_summary/PRESENTATION_OUTPUT.json')
code = {str(p): sha(p) for p in [BASE/'code/algorithm_validation.py', BASE/'code/robustness_refinement.py', BASE/'code/finalize_validation_audit.py',
                                 BASE/'boss_summary/.build/build_presentation.mjs', ROOT/'scripts/nmr/run_algorithm_validation.py']}
record = {'completed_utc': datetime.now(timezone.utc).isoformat(), 'passed': reviews_passed,
    'review_stages': {'expected_note_numbers': list(STAGE_NOTES), 'pending_note_numbers': pending, 'not_approved': not_approved},
    'integrity': {k: (len(v) if isinstance(v, list) else v) for k, v in integrity.items()},
    'tests': tests, 'tests_total': sum(t['tests'] for t in tests),
    'figures': len(figures), 'figure_hash_and_title_failures': 0,
    'review_notes': [{'path': str(n), 'sha256': sha(n), 'verdict': verdicts[n.name]} for n in notes],
    'deck': {'path': deck['path'], 'sha256': sha(deck['path']), 'slides': deck['slide_count'], 'receipt_path': deck['receipt_path']},
    'code_sha256': code,
    'interpretation': 'Counts are grouped evidence checks, not independent experiments or proof of scientific accuracy.'}
(BASE/'verification/FINAL_AUDIT_RESULTS.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
i = record['integrity']
lines = ['# Final verification', '',
    f'Completed {record["completed_utc"]}. Every statement below is computed from the current evidence files by `code/finalize_validation_audit.py`.', '',
    ('**Overall: passed.** Integrity, tests, figure hashes/titles and all seven refinement-stage reviews (notes 08–14) pass.' if reviews_passed else
     f'**Overall: not yet passed.** Pending stage notes: {pending or "none"}; not approved: {not_approved or "none"}. '
     'Integrity, tests and figure checks below passed; the audit stays open until every refinement-stage review is approved.'), '',
    f'- **Integrity:** passed. {i["verified_unchanged_count"]:,} of {i["raw_and_historical_file_count"]:,} protected raw/historical files re-hashed unchanged; '
    f'{i["unverifiable_access_denied_files"]:,} files in {i["unreadable_directories"]} pytest temp/cache directories are access-denied for this account and '
    'are reported as unverifiable within the reviewed baseline `verification/ACCESS_DENIED_BASELINE.json`, not as unchanged. '
    f'Changed {i["changed_frozen_files"]}, missing {i["missing_frozen_files"]}, no longer a file {i["no_longer_a_file"]}, denied outside the baseline '
    f'{i["access_denied_outside_baseline"]}, new unreadable directories {i["new_unreadable_directories"]}, added {i["added_historical_or_raw_files"]}, '
    f'scientific sources changed {i["changed_scientific_sources"]}.',
    f'- **Tests:** {record["tests_total"]} passed, 0 failed, across ' + ', '.join(f'{Path(t["file"]).stem} ({t["tests"]})' for t in tests) + '.',
    f'- **Figures:** {record["figures"]} manifest entries; every file hash matches and every visible title starts with `{dataset}` exactly once.',
    f'- **Presentation:** {record["deck"]["slides"]} slides, `{record["deck"]["path"]}` (receipt `{record["deck"]["receipt_path"]}`).',
    f'- **Independent review notes:** {len(notes)} files.']
lines += [f'  - `{n["path"].split(chr(92))[-1]}`: {n["verdict"]}' for n in record['review_notes'] if not n['verdict'].startswith('v1')]
lines += ['', 'v1 notes (01–07) are retained unchanged. Scientific accuracy, practical equivalence and a chemical endpoint remain unproven.']
(BASE/'verification/FINAL_AUDIT_RESULTS.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
print(json.dumps({'passed': reviews_passed, 'pending': pending, 'not_approved': not_approved, 'tests': record['tests_total'], 'figures': record['figures'], 'notes': len(notes),
                  'verified': i['verified_unchanged_count'], 'unverifiable': i['unverifiable_access_denied_files']}))
