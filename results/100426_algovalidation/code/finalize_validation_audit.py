"""Collect the final audit record from current evidence files; every check is computed and fails closed, none is hard-coded.

v1 of this script and its outputs are archived in logs/superseded_v1_20261004/.
"""
from pathlib import Path
import csv, json, hashlib, re, zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE.parents[1]
V1_AUDIT = 'logs/superseded_v1_20261004/verification/FINAL_AUDIT_RESULTS.json'
CODE_UNDER_TEST = [BASE/'code/algorithm_validation.py', BASE/'code/robustness_refinement.py']


def js(p): return json.loads((BASE/p).read_text(encoding='utf-8-sig'))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def junit(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == 'testsuite' else list(root)
    count = lambda k: sum(int(s.get(k, 0)) for s in suites)
    return {'file': str(path), 'tests': count('tests'), 'failures': count('failures'), 'errors': count('errors'), 'skipped': count('skipped')}


# Integrity (raw and historical files, scientific sources)
integrity = js('verification/FINAL_INTEGRITY_CHECK.json')
assert integrity['passed'], 'integrity check did not pass'
baseline = js('verification/ACCESS_DENIED_BASELINE.json')

# Tests: no failures, errors or skips, and every result is newer than the code and the test file it covers
tests = []
for p in sorted((BASE/'verification').glob('junit_*.xml')):
    t = junit(p); test_file = BASE/'verification'/f'test_{p.stem[len("junit_"):]}.py'
    newest = max(f.stat().st_mtime for f in CODE_UNDER_TEST+[test_file])
    t['passed'] = t['tests']-t['failures']-t['errors']-t['skipped']
    t['newer_than_code_and_tests'] = p.stat().st_mtime >= newest
    tests.append(t)
assert tests and all(t['failures'] == t['errors'] == t['skipped'] == 0 and t['newer_than_code_and_tests'] for t in tests), tests

# Figures: one dataset identity, hashes and visible titles
with (BASE/'logs/figure_manifest.csv').open(encoding='utf-8-sig', newline='') as f:
    figures = list(csv.DictReader(f))
datasets = {r['dataset_display_name'] for r in figures}
assert len(datasets) == 1, datasets
dataset = datasets.pop()
bad = [r['path'] for r in figures if sha(r['path']) != r['sha256'] or not r['visible_title'].startswith(dataset+' ')
       or r['visible_title'].count(dataset) != 1]
assert not bad, bad

# Review notes: v1 notes 01-07 must match the hashes in the v1 audit record; refinement stages 1-7 are notes 08-14
notes = sorted((BASE/'verification').glob('[0-9][0-9]_*_review.md'))
v1_expected = {Path(r['path']).name: r['sha256'] for r in js(V1_AUDIT)['independent_review_notes']}
v1_current = {n.name: sha(n) for n in notes if int(n.name[:2]) < 8}
v1_mismatch = sorted(k for k in set(v1_expected) | set(v1_current) if v1_expected.get(k) != v1_current.get(k))
assert not v1_mismatch, f'v1 review notes differ from the v1 audit record: {v1_mismatch}'
verdicts = {}
for n in notes:
    text_lines = [x.strip() for x in n.read_text(encoding='utf-8').splitlines() if x.strip()]
    if int(n.name[:2]) >= 8:  # the final line governs (earlier verdicts are superseded history); a note without one fails
        assert text_lines and text_lines[-1].startswith('VERDICT:'), f'{n.name} must end with its VERDICT line'
        verdicts[n.name] = text_lines[-1].split(':', 1)[1].strip()
    else:
        verdicts[n.name] = 'v1 stage note (see file)'
STAGE_NOTES = range(8, 15)
present = {int(n.name[:2]) for n in notes if int(n.name[:2]) in STAGE_NOTES}
pending = [k for k in STAGE_NOTES if k not in present]
not_approved = [n.name for n in notes if int(n.name[:2]) in STAGE_NOTES and not verdicts[n.name].upper().startswith('APPROVED')]
reviews_passed = not pending and not not_approved

# Presentation: slides counted from the file; the validation receipt must describe this exact file
deck = js('boss_summary/PRESENTATION_OUTPUT.json')
with zipfile.ZipFile(deck['path']) as z:
    slides = sum(bool(re.fullmatch(r'ppt/slides/slide\d+\.xml', name)) for name in z.namelist())
receipt = json.loads(Path(deck['receipt_path']).read_text(encoding='utf-8'))
deck_sha = sha(deck['path'])
assert slides == deck['slide_count'] and receipt['finalSha256'] == deck_sha, (slides, deck['slide_count'], receipt['finalSha256'], deck_sha)

code = {str(p): sha(p) for p in [*CODE_UNDER_TEST, BASE/'code/finalize_validation_audit.py',
                                 BASE/'boss_summary/.build/build_presentation.mjs', ROOT/'scripts/nmr/run_algorithm_validation.py']}
record = {'completed_utc': datetime.now(timezone.utc).isoformat(), 'passed': reviews_passed,
    'review_stages': {'expected_note_numbers': list(STAGE_NOTES), 'pending_note_numbers': pending, 'not_approved': not_approved},
    'integrity': {k: (len(v) if isinstance(v, list) else v) for k, v in integrity.items()},
    'tests': tests, 'tests_total': sum(t['tests'] for t in tests), 'tests_passed': sum(t['passed'] for t in tests),
    'figures': len(figures), 'dataset_display_name': dataset, 'figure_hash_and_title_failures': len(bad),
    'v1_review_notes_matching_v1_audit_record': len(v1_current), 'v1_review_notes_in_v1_audit_record': len(v1_expected),
    'review_notes': [{'path': str(n), 'sha256': sha(n), 'verdict': verdicts[n.name]} for n in notes],
    'deck': {'path': deck['path'], 'sha256': deck_sha, 'slides_counted_in_file': slides, 'receipt_path': deck['receipt_path'],
             'receipt_matches_file': receipt['finalSha256'] == deck_sha},
    'code_sha256': code,
    'interpretation': 'Counts are grouped evidence checks, not independent experiments or proof of scientific accuracy.'}
(BASE/'verification/FINAL_AUDIT_RESULTS.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
i = record['integrity']
folders = baseline['reason'].split(' by ')[0]
lines = ['# Final verification', '',
    f'Completed {record["completed_utc"]}. Every check below is computed from the current evidence files by `code/finalize_validation_audit.py` '
    'and fails closed; only the final caveat is interpretation.', '',
    ('**Overall: passed.** Integrity, tests, figure hashes/titles, the presentation receipt and all seven refinement-stage reviews (notes 08–14) pass.'
     if reviews_passed else
     f'**Overall: not yet passed.** Pending stage notes: {pending or "none"}; not approved: {not_approved or "none"}. '
     'Integrity, tests, figure and presentation checks below passed; the audit stays open until every refinement-stage review is approved.'), '',
    f'- **Integrity:** passed. {i["verified_unchanged_count"]:,} of {i["raw_and_historical_file_count"]:,} protected raw/historical files re-hashed unchanged; '
    f'{i["unverifiable_access_denied_files"]:,} files in {i["unreadable_directories"]} directories ({folders}) are access-denied for this account and '
    'are reported as unverifiable within the reviewed baseline `verification/ACCESS_DENIED_BASELINE.json`, not as unchanged. '
    f'Changed {i["changed_frozen_files"]}, missing {i["missing_frozen_files"]}, no longer a file {i["no_longer_a_file"]}, denied outside the baseline '
    f'{i["access_denied_outside_baseline"]}, new unreadable directories {i["new_unreadable_directories"]}, added {i["added_historical_or_raw_files"]}, '
    f'scientific sources changed {i["changed_scientific_sources"]}.',
    f'- **Tests:** {record["tests_passed"]} of {record["tests_total"]} passed (0 failed, 0 errors, 0 skipped), across '
    + ', '.join(f'{Path(t["file"]).stem} ({t["passed"]})' for t in tests) + '; every result is newer than the code and test file it covers.',
    f'- **Figures:** {record["figures"]} manifest entries, all for dataset `{dataset}`; {len(bad)} hash or title failures (every visible title starts with '
    f'`{dataset}` exactly once).',
    f'- **Presentation:** {slides} slides counted in `{record["deck"]["path"]}`; the validation receipt `{record["deck"]["receipt_path"]}` records this exact '
    'file (SHA-256 match).',
    f'- **Independent review notes:** {len(notes)} files. The {len(v1_current)} v1 notes (01–07) match the hashes in the v1 audit record `{V1_AUDIT}`.']
lines += [f'  - `{n["path"].split(chr(92))[-1]}`: {n["verdict"]}' for n in record['review_notes'] if not n['verdict'].startswith('v1')]
lines += ['', 'Caveat (interpretation): scientific accuracy, practical equivalence and a chemical endpoint remain unproven.']
(BASE/'verification/FINAL_AUDIT_RESULTS.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
print(json.dumps({'passed': reviews_passed, 'pending': pending, 'not_approved': not_approved, 'tests': record['tests_passed'],
                  'figures': record['figures'], 'notes': len(notes), 'v1_notes_verified': len(v1_current),
                  'verified': i['verified_unchanged_count'], 'unverifiable': i['unverifiable_access_denied_files']}))
