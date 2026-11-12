"""Rebuild the immutable Spark projection from bounded verified gzip parts."""
import gzip
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
ROOT = parser.parse_args().root.resolve()
archive_root = ROOT / 'deploy/index'
manifest = json.loads((archive_root / 'archive.json').read_text(encoding='utf-8'))
destination = ROOT / 'data/index/search.sqlite3'
destination.parent.mkdir(parents=True, exist_ok=True)

def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()

if destination.exists() and digest(destination) == manifest['sqlite_sha256']:
    print(json.dumps({'index_restored': True, 'already_verified': True, 'bytes': destination.stat().st_size}))
else:
    compressed = archive_root / 'search.sqlite3.gz.restoring'
    restored = destination.with_suffix('.sqlite3.restoring')
    with compressed.open('wb') as target:
        for part in manifest['parts']:
            name = part['filename']
            if Path(name).name != name or not name.startswith('search.sqlite3.gz.part-'):
                raise ValueError('Invalid index archive part name')
            source = archive_root / name
            if source.stat().st_size != part['size_bytes'] or digest(source) != part['sha256']:
                raise ValueError('Index archive part failed size/SHA-256 verification: ' + name)
            with source.open('rb') as handle:
                shutil.copyfileobj(handle, target, 1024 * 1024)
    if digest(compressed) != manifest['gzip_sha256']:
        raise ValueError('Joined gzip SHA-256 mismatch')
    with gzip.open(compressed, 'rb') as source, restored.open('wb') as target:
        shutil.copyfileobj(source, target, 1024 * 1024)
    if restored.stat().st_size != manifest['sqlite_bytes'] or digest(restored) != manifest['sqlite_sha256']:
        raise ValueError('Restored SQLite SHA-256 mismatch')
    with sqlite3.connect(restored.as_uri() + '?mode=ro', uri=True) as connection:
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Restored SQLite quick_check failed')
        count = connection.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
        if count != manifest['book_count']:
            raise ValueError('Restored document count mismatch')
    connection.close() # SQLite's context manager commits but does not close on Windows.
    os.replace(restored, destination)
    # Both are task-generated intermediates, never original corpus/source files.
    compressed.unlink()
    print(json.dumps({'index_restored': True, 'books': count, 'bytes': destination.stat().st_size, 'sha256': manifest['sqlite_sha256']}))
