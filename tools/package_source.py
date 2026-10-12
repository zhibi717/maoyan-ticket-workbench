"""Package the Maoyan source without local profiles or generated phone files."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {'.py', '.js', '.html', '.css', '.md', '.json', '.jsonc', '.toml'}
TOP = ('README.md', 'run.py', 'Start.bat', 'setup.bat', 'Test.bat',
       'requirements.txt', 'requirements-lock.txt')
TOOLS = ('package_source.py', 'phone_readonly_inspect.py',
         'autox_first_click_benchmark.js', 'platform-tools/README.md')
IGNORE = '''# Local runtime and private data
.venv/
__pycache__/
*.py[cod]
data/*
!data/.gitkeep
.env
.env.*
*.log
*.lock
*.png
*.jpg
*.jpeg
*.zip
tools/platform-tools/*
!tools/platform-tools/README.md
.DS_Store
.venv-local/
cityline/
'''


def private_values() -> set[str]:
    path = ROOT / 'data' / 'profiles.json'
    if not path.exists():
        return set()
    profiles = json.loads(path.read_text(encoding='utf-8'))
    values = set()
    for profile in profiles:
        values.update(s for s in profile.get('users', []) if len(s) >= 2)
        if profile.get('serial'):
            values.add(profile['serial'])
    return values


def validate_text(name: str, text: str, private: set[str]) -> None:
    if any(value in text for value in private):
        raise ValueError(f'Private configuration found in {name}; package stopped.')
    if re.search(r'github_pat_[A-Za-z0-9_]{15,}|gh[pousr]_[A-Za-z0-9]{20,}|'
                 r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', text):
        raise ValueError(f'Credential text found in {name}; package stopped.')


def prepare(stage: Path) -> dict:
    stage = stage.resolve()
    if stage.exists() and any(stage.iterdir()):
        raise ValueError('The package staging directory must be new or empty.')
    files = [ROOT / name for name in TOP]
    for directory in ('autox', 'ticketdesk', 'tests', 'config', 'docs', 'vendor'):
        for path in (ROOT / directory).rglob('*'):
            if (path.is_file() and not any(p in ('.git', '__pycache__', 'node_modules')
                                           for p in path.relative_to(ROOT).parts)
                    and (path.suffix in SUFFIXES or path.name in ('LICENSE', 'NOTICE'))):
                files.append(path)
    files.extend(ROOT / 'tools' / name for name in TOOLS)
    private = private_values()
    contents = {path.relative_to(ROOT).as_posix():
                path.read_text(encoding='utf-8-sig').encode('utf-8') for path in files}
    contents['.gitignore'] = IGNORE.encode('utf-8')
    contents['data/.gitkeep'] = b''
    for name, raw in contents.items():
        validate_text(name, raw.decode('utf-8'), private)
    manifest = {'format': 1, 'kind': 'source-only', 'files': []}
    for name, raw in sorted(contents.items()):
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        manifest['files'].append({'path': name, 'bytes': len(raw),
                                  'sha256': hashlib.sha256(raw).hexdigest(),
                                  'git_blob_sha': hashlib.sha1(
                                      b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()})
    (stage / 'PACKAGE-MANIFEST.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def archive(stage: Path, output: Path, commit: str | None = None) -> dict:
    manifest = json.loads((stage / 'PACKAGE-MANIFEST.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 1 or manifest.get('kind') != 'source-only':
        raise ValueError('Unrecognized package manifest.')
    if commit is not None and not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Invalid GitHub commit SHA.')
    manifest['source_commit'] = commit
    names = []
    private = private_values()
    for item in manifest['files']:
        name = item['path']
        path = (stage / name).resolve()
        if not path.is_relative_to(stage.resolve()):
            raise ValueError('Unsafe archive path.')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError(f'Source changed since staging: {name}')
        validate_text(name, raw.decode('utf-8'), private)
        names.append((name, raw))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, raw in names:
            z.writestr('maoyan-ticket-workbench/' + name, raw)
        z.writestr('maoyan-ticket-workbench/PACKAGE-MANIFEST.json',
                   json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    with zipfile.ZipFile(temporary) as z:
        if z.testzip() is not None or len(z.namelist()) != len(names) + 1:
            raise ValueError('Archive verification failed.')
    temporary.replace(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(f'{digest}  {output.name}\n', encoding='utf-8')
    return {'archive': str(output), 'bytes': output.stat().st_size,
            'source_files': len(names), 'sha256': digest, 'source_commit': commit,
            'private_configuration_included': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', type=Path, metavar='STAGE')
    group.add_argument('--zip-stage', type=Path, metavar='STAGE')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--commit')
    args = parser.parse_args()
    if args.prepare:
        report = prepare(args.prepare)
        print(json.dumps({'stage': str(args.prepare.resolve()),
                          'source_files': len(report['files']), 'privacy_scan_passed': True}))
    else:
        if args.output is None:
            parser.error('--output is required with --zip-stage')
        print(json.dumps(archive(args.zip_stage, args.output, args.commit), ensure_ascii=True))


if __name__ == '__main__':
    main()
