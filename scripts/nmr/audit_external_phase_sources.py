"""Download public reference sources into an isolated audit directory; execute none."""
import hashlib
import json
from pathlib import Path
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'results/NMR_validation_100226/logs/additional_phase_methods/external_audit'


def fetch(url, path):
    request = urllib.request.Request(url, headers={'User-Agent': 'NMR-local-research-audit'})
    data = urllib.request.urlopen(request, timeout=30).read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    manifest = []
    for repo in ('phase_1d', 'colmarvista'):
        metadata = json.loads(fetch(f'https://api.github.com/repos/lidawei1975/{repo}', DEST/f'{repo}_repository.json'))
        branch = metadata['default_branch']
        tree = json.loads(fetch(f'https://api.github.com/repos/lidawei1975/{repo}/git/trees/{branch}?recursive=1', DEST/f'{repo}_tree.json'))
        for row in tree['tree']:
            name = row['path']
            include = repo == 'phase_1d' or name.startswith(('saved_model_p0/', 'saved_model_p1/', 'js/')) or name in ('LICENSE', 'README.md', 'package.json', 'index_1d.html')
            if row['type'] != 'blob' or not include or row.get('size', 0) > 30_000_000:
                continue
            path = (DEST/repo/name).resolve()
            if not path.is_relative_to(DEST.resolve()):
                raise ValueError('Source path outside isolated destination')
            url = f'https://raw.githubusercontent.com/lidawei1975/{repo}/{tree["sha"]}/'+urllib.parse.quote(name)
            data = path.read_bytes() if path.exists() else fetch(url, path)
            manifest.append({'url': url, 'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'repository_commit': tree['sha']})
        print(repo, 'source files obtained', flush=True)
    (DEST/'download_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    runtime = DEST/'tfjs-4.22.0.cjs'
    if not runtime.exists():
        fetch('https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@4.22.0/dist/tf.min.js', runtime)
    (DEST/'runtime_manifest.json').write_text(json.dumps({'url': 'https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@4.22.0/dist/tf.min.js', 'version': '4.22.0', 'path': str(runtime.resolve()), 'sha256': hashlib.sha256(runtime.read_bytes()).hexdigest()}, indent=2), encoding='utf-8')
    for filename in ('tf-backend-wasm.js','tfjs-backend-wasm.wasm','tfjs-backend-wasm-simd.wasm','tfjs-backend-wasm-threaded-simd.wasm'):
        destination=DEST/'wasm'/filename
        if not destination.exists():
            fetch('https://cdn.jsdelivr.net/npm/@tensorflow/tfjs-backend-wasm@4.22.0/dist/'+filename,destination)
    print('Downloaded', len(manifest), 'files; no external code executed.', flush=True)


if __name__ == '__main__':
    main()
