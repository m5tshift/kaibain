"""Offline data transfer; never imports Store, invokes Git, or executes a project.

All three public functions return the verified JSON manifest (and raise
BackupError, a ValueError, for invalid/unsupported data). destination is a new
ZIP filename. destination_db must be inside a NEW directory whose parent already
exists. Restoring publishes that entire directory with one no-replace rename;
existing directories, even empty ones, are deliberately refused.

Only the SQLite snapshot and regular .agent-runs files are transferred. Local
configuration/credential names are excluded, not followed. Arbitrary secrets in
database text or artifact contents cannot be detected: treat backups as private.
SHA256 detects corruption, not a malicious party rewriting the whole archive.
Use a trusted local filesystem; a hostile process controlling ancestor directories
is outside this API's concurrency contract. DB writers must use SQLite locking;
artifacts must be immutable after a run leaves queued/running/cancelling.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import struct
import sys
import tempfile
import time
import unicodedata
import zipfile


FORMAT = 'kaibain-backup'
VERSION = 1
DATABASE = 'database.sqlite3'
MAX_FILES = 20000
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_DB_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_CENTRAL_BYTES = 16 * 1024 * 1024
MAX_RATIO = 200
MAX_SECONDS = 120
CHUNK = 1024 * 1024
_PRIVATE = {'.venv', 'venv', '.mcp', '.mcp.json', '.local', '.git', '.ssh',
            'config', 'configs', 'secrets', 'credentials', 'id_rsa', 'id_ed25519',
            'agent-runtime.json', 'codex.toml'}
_DEVICE = re.compile(r'^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)', re.I)
_SHA = re.compile('[0-9a-f]{64}\\Z')


class BackupError(ValueError):
    """Fail closed without changing existing destination data."""


def _require(condition, message):
    if not condition:
        raise BackupError(message)


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(BackupError('Invalid JSON number')))
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise BackupError('Invalid JSON: ' + str(error)[:160]) from error


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('ascii')


def _path(value):
    path = Path(os.path.abspath(value))  # Do not resolve away links before checking.
    _guard(path)
    return path


def _guard(path):
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        _require(not stat.S_ISLNK(info.st_mode) and not
                 (getattr(info, 'st_file_attributes', 0) & 0x400),
                 'Symlinks/junctions/reparse points are forbidden: ' + str(part))


def _regular(path, limit):
    _guard(path)
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
             'Expected a regular, unlinked file: ' + str(path))
    _require(info.st_size <= limit, 'File size limit exceeded: ' + str(path))
    return info


def _stamp(info):
    # Windows stat/fstat can expose different legacy ctime meanings after
    # copystat/chmod. File identity, bytes, mtime and birthtime are consistent.
    changed = getattr(info,'st_birthtime_ns',info.st_ctime_ns) if os.name == 'nt' else info.st_ctime_ns
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, changed)


def _private(part):
    name = part.casefold()
    return (name in _PRIVATE or name == '.env' or name.startswith('.env.') or
            name.endswith(('.pem', '.key', '.p12', '.pfx')))


def _name(name):
    _require(isinstance(name, str) and 0 < len(name.encode('utf-8')) <= 1024,
             'Invalid archive path')
    parts = name.split('/')
    _require(len(parts) <= 32, 'Path depth limit exceeded')
    for part in parts:
        _require(part not in ('', '.', '..') and len(part.encode('utf-8')) <= 255 and
                 not part.endswith(('.', ' ')) and not _DEVICE.match(part) and
                 not any(ord(c) < 32 or c in '\\:<>"|?*\x7f' for c in part),
                 'Unsafe archive path: ' + name)
    _require(name == DATABASE or (len(parts) >= 3 and parts[0] == '.agent-runs'),
             'Unexpected archive member: ' + name)
    _require(not any(_private(part) for part in parts), 'Private configuration in archive')
    return unicodedata.normalize('NFC', name).casefold()


def _inventory(root):
    files = {}
    _guard(root)
    if not root.exists():
        return files
    _require(root.is_dir(), '.agent-runs must be a directory')
    count = 0

    def walk(folder, depth):
        nonlocal count
        _require(depth <= 30, 'Artifact path depth limit exceeded')
        with os.scandir(folder) as entries:
            for entry in entries:
                count += 1
                _require(count <= MAX_FILES, 'Artifact entry limit exceeded')
                path = Path(entry.path)
                _guard(path)
                info = path.lstat()
                _require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),
                         'Non-regular artifact: ' + str(path))
                if _private(entry.name):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    walk(path, depth + 1)
                else:
                    name = '.agent-runs/' + path.relative_to(root).as_posix()
                    _name(name)
                    files[name] = _stamp(_regular(path, MAX_FILE_BYTES))
    walk(root, 0)
    _require(sum(value[2] for value in files.values()) <= MAX_TOTAL_BYTES, 'Total size limit exceeded')
    _unique_names(files)
    return files


def _unique_names(names):
    seen = set()
    for name in names:
        key = _name(name)
        _require(key not in seen, 'Duplicate/case/Unicode path collision')
        seen.add(key)
    for name in seen:
        parts = name.split('/')
        _require(not any('/'.join(parts[:i]) in seen for i in range(1, len(parts))),
                 'File/directory path collision')


@contextmanager
def _connection(path, writable=False):
    uri = path.as_uri() + ('?mode=rw' if writable else '?mode=ro&immutable=1')
    # immutable is only used for a private, completed snapshot, never a live DB.
    with closing(sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        db.enable_load_extension(False)
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_DB_BYTES)
        start = time.monotonic()
        calls = 0

        def bounded():
            nonlocal calls
            calls += 1
            return int(calls > 100000 or time.monotonic() - start > MAX_SECONDS)
        db.set_progress_handler(bounded, 1000)
        try:
            yield db
        except sqlite3.Error as error:
            raise BackupError('SQLite validation/locking failed: ' + str(error)) from error
        finally:
            if db.in_transaction:
                db.rollback()


def _receipt(db):
    schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    _require(not any('VIRTUAL TABLE' in (row[3] or '').upper() for row in schema),
             'Virtual tables are not supported')
    tables = {row[1] for row in schema if row[0] == 'table'}
    _require({'meta', 'tasks', 'agent_runs', 'run_checkpoints', 'run_applications'} <= tables,
             'Unsupported Kaibain schema')
    user_version = db.execute('PRAGMA user_version').fetchone()[0]
    application_id = db.execute('PRAGMA application_id').fetchone()[0]
    _require(user_version == 0 and application_id == 0, 'Unsupported database version')
    _require(not db.execute("SELECT 1 FROM agent_runs WHERE status IN ('queued','running','cancelling') LIMIT 1").fetchone(),
             'Backup/restore forbidden while a run is queued/running/cancelling')
    if 'run_cleanups' in tables:
        _require(not db.execute("SELECT 1 FROM run_cleanups WHERE status='running' LIMIT 1").fetchone(),
                 'Backup/restore forbidden while owned cleanup is running')
    revision = db.execute('SELECT revision FROM meta WHERE id=1').fetchone()
    _require(revision is not None, 'Missing workspace revision')
    # A logical digest covers all rows, including versions not tracked by meta,
    # checkpoint receipts, application receipts and immutable project snapshots.
    digest = hashlib.sha256(_encoded(schema))
    for table in sorted(tables):
        quoted = '"' + table.replace('"', '""') + '"'
        columns = db.execute('PRAGMA table_info(' + quoted + ')').fetchall()
        order = ','.join(str(i + 1) for i in range(len(columns)))
        digest.update(_encoded(table))
        for row in db.execute('SELECT * FROM ' + quoted + ' ORDER BY ' + order):
            digest.update(_encoded([{'blob': item.hex()} if isinstance(item, bytes) else item for item in row]))
            digest.update(b'\n')
    return {'user_version': user_version, 'application_id': application_id,
            'revision': revision[0], 'logical_sha256': digest.hexdigest(),
            'schema_sha256': hashlib.sha256(_encoded(schema)).hexdigest()}


def _db_checks(db):
    _require(db.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'SQLite integrity check failed')
    _require(db.execute('PRAGMA foreign_key_check').fetchone() is None, 'SQLite foreign key check failed')


def _small(path, limit=MAX_MANIFEST_BYTES):
    _regular(path, limit)
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    _require(len(raw) <= limit, 'JSON size limit exceeded')
    return raw


def _match(path, expected_hash, expected_bytes=None):
    _require(isinstance(expected_hash, str) and _SHA.fullmatch(expected_hash), 'Invalid SHA256 receipt')
    info = _regular(path, MAX_FILE_BYTES)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
    _require(digest.hexdigest() == expected_hash and
             (expected_bytes is None or (type(expected_bytes) is int and info.st_size == expected_bytes)),
             'Artifact receipt mismatch: ' + str(path))


def _export(root, manifest_hash, verification=None, project=None):
    manifest_path = root / 'manifest.json'
    _match(manifest_path, manifest_hash)
    manifest = _json(_small(manifest_path, 65536))
    _require(isinstance(manifest, dict) and manifest.get('backend') == 'microsandbox' and
             manifest.get('isolation') == 'microvm' and not manifest.get('deleted'), 'Invalid export manifest')
    if project:
        _require(manifest.get('base_commit') == project.get('commit'), 'Export/source commit mismatch')
    _match(root / 'changes.diff', manifest.get('patch_sha256'))
    entries = manifest.get('files')
    _require(isinstance(entries, list) and 1 <= len(entries) <= 40, 'Invalid export files')
    seen = set()
    for entry in entries:
        _require(isinstance(entry, dict), 'Invalid export entry')
        name = entry.get('path')
        key = _name('.agent-runs/export/files/' + name) if isinstance(name, str) else None
        _require(key is not None and key not in seen, 'Duplicate/invalid export path')
        seen.add(key)
        if project:
            _require(name in project.get('files', []) + project.get('writable', []), 'Export path outside profile')
        _match(root / 'files' / name, entry.get('sha256'), entry.get('bytes'))
    if project:
        _require(set(project.get('files', [])) <= {entry['path'] for entry in entries}, 'Incomplete export')
    if verification:
        _require(verification.get('patch_sha256') == manifest.get('patch_sha256'), 'Result diff receipt mismatch')
        if verification.get('report_sha256'):
            _match(root.parent / 'report.json', verification['report_sha256'])


def _links(db, root):
    runs = {}
    for run_id, raw_result, raw_project in db.execute('SELECT id,result,project FROM agent_runs'):
        _name('.agent-runs/' + run_id + '/placeholder')
        _require('/' not in run_id, 'Unsafe run ID')
        runs[run_id] = _json(raw_project) if raw_project else None
        if raw_result:
            result = _json(raw_result)
            _require(isinstance(result, dict), 'Invalid saved result')
            verification = result.get('verification') or {}
            if verification.get('manifest_sha256'):
                _export(root / '.agent-runs' / run_id / 'development' / 'result',
                        verification['manifest_sha256'], verification, runs[run_id])
    for run_id, sequence, manifest_hash in db.execute('SELECT run_id,sequence,manifest_sha256 FROM run_checkpoints'):
        _require(run_id in runs and type(sequence) is int and 0 <= sequence <= 999999, 'Invalid checkpoint link')
        _export(root / '.agent-runs' / run_id / 'development' / 'checkpoints' / f'{sequence:03d}',
                manifest_hash, project=runs[run_id])
    for run_id, raw in db.execute('SELECT run_id,receipt FROM run_applications'):
        receipt = _json(raw)
        _require(run_id in runs and isinstance(receipt, dict) and receipt.get('run_id') == run_id,
                 'Invalid application link')
        _match(root / '.agent-runs' / run_id / 'development' / 'result' / 'manifest.json',
               receipt.get('manifest_sha256'))
    for (checkpoint_id,) in db.execute('SELECT recovery_checkpoint_id FROM agent_runs WHERE recovery_checkpoint_id IS NOT NULL'):
        _require(db.execute('SELECT 1 FROM run_checkpoints WHERE id=?', (checkpoint_id,)).fetchone(),
                 'Missing inherited checkpoint receipt')
    if 'checkpoint_id' in {row[1] for row in db.execute('PRAGMA table_info(goal_questions)')}:
        for run_id,checkpoint_id in db.execute('SELECT run_id,checkpoint_id FROM goal_questions WHERE checkpoint_id IS NOT NULL'):
            _require(db.execute('SELECT 1 FROM run_checkpoints WHERE id=? AND run_id=?',(checkpoint_id,run_id)).fetchone(),
                     'Missing question checkpoint receipt')


def _validate(root, manifest):
    with _connection(root / DATABASE) as db:
        receipt = _receipt(db)
        _db_checks(db)
        _require(receipt == manifest['consistency'], 'Database consistency/version receipt mismatch')
        _links(db, root)


def _rename_new(source, destination):
    """Atomic directory rename without POSIX rename's empty-dir overwrite."""
    _guard(destination)
    _require(not os.path.lexists(destination), 'Destination already exists: ' + str(destination))
    if os.name == 'nt':
        os.rename(source, destination)  # Windows rename never replaces a target.
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith('linux'):
        function = getattr(libc, 'renameat2', None)
        _require(function is not None, 'Atomic no-replace rename unavailable')
        result = function(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    elif sys.platform == 'darwin':
        result = libc.renamex_np(os.fsencode(source), os.fsencode(destination), 4)  # RENAME_EXCL
    else:
        raise BackupError('Atomic no-replace rename unavailable on this platform')
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def create_backup(db_path, destination):
    """Create a new ZIP, holding BEGIN IMMEDIATE until artifacts are sealed.

    Idle web/MCP readers may remain up; writers wait (or time out). No source
    rows are changed. Missing DB, active runs, unsafe/mutating artifacts and an
    existing destination are errors. The parent of destination must exist.
    """
    source, destination = _path(db_path), _path(destination)
    _regular(source, MAX_DB_BYTES)
    _require(destination.parent.is_dir() and not os.path.lexists(destination), 'Destination must be a new file in an existing directory')
    artifacts = source.parent / '.agent-runs'
    _require(not destination.is_relative_to(artifacts), 'Backup destination cannot be inside artifacts')
    with tempfile.TemporaryDirectory(prefix='.kaibain-backup-', dir=destination.parent) as temporary:
        stage = Path(temporary)
        with _connection(source, writable=True) as lock:
            lock.execute('BEGIN IMMEDIATE')
            receipt = _receipt(lock)
            _db_checks(lock)
            inventory = _inventory(artifacts)
            # Backing up the connection owning a write transaction can deadlock.
            # A separate live read connection sees the committed DB under our lock.
            with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)) as reader:
                with closing(sqlite3.connect(stage / DATABASE)) as snapshot:
                    reader.backup(snapshot)
                    snapshot.execute('PRAGMA journal_mode=DELETE')
            (stage / '.agent-runs').mkdir()
            for name, before in inventory.items():
                path = source.parent / name
                _require(_stamp(_regular(path, MAX_FILE_BYTES)) == before, 'Artifacts changed during backup')
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with path.open('rb') as incoming, target.open('xb') as outgoing:
                    _require(_stamp(os.fstat(incoming.fileno())) == before, 'Artifact replaced during open')
                    copied = 0
                    while chunk := incoming.read(CHUNK):
                        copied += len(chunk)
                        _require(copied <= before[2], 'Artifact grew during backup')
                        outgoing.write(chunk)
                    _require(copied == before[2] and _stamp(os.fstat(incoming.fileno())) == before,
                             'Artifact changed during copy')
            _require(inventory == _inventory(artifacts) and receipt == _receipt(lock),
                     'Source consistency changed during backup')
            files = []
            for name in [DATABASE, *sorted(inventory)]:
                path = stage / name
                info = _regular(path, MAX_FILE_BYTES)
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    while chunk := stream.read(CHUNK):
                        digest.update(chunk)
                files.append({'path': name, 'bytes': info.st_size, 'sha256': digest.hexdigest()})
            _require(len(files) <= MAX_FILES and sum(entry['bytes'] for entry in files) <= MAX_TOTAL_BYTES,
                     'Backup size/file count limit exceeded')
            manifest = {'format': FORMAT, 'version': VERSION, 'database': DATABASE,
                        'created_at': datetime.now(timezone.utc).isoformat(),
                        'consistency': receipt, 'files': files}
            _require(len(_encoded(manifest)) <= MAX_MANIFEST_BYTES, 'Manifest size limit exceeded')
            _validate(stage, manifest)
            # ZIP_STORED makes every archive we produce pass the bomb ratio guard,
            # even when artifacts contain long zero-filled/log/repeated text files.
            archive = stage / 'backup.zip'
            with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_STORED, allowZip64=False) as output:
                output.writestr('manifest.json', _encoded(manifest))
                for entry in files:
                    output.write(stage / entry['path'], entry['path'])
            _require(archive.stat().st_size <= MAX_ARCHIVE_BYTES, 'Archive size limit exceeded')
            # Hard-link publication is atomic and cannot overwrite an existing file.
            # The staging link is removed with our private temporary directory.
            with archive.open('r+b') as stream:
                os.fsync(stream.fileno())
            _guard(destination)
            os.link(archive, destination)
        return manifest


def _preflight(stream):
    stream.seek(0, 2)
    size = stream.tell()
    _require(22 <= size <= MAX_ARCHIVE_BYTES, 'Archive size limit exceeded/invalid ZIP')
    stream.seek(max(0, size - 65557))
    tail = stream.read(65557)
    offset = tail.rfind(b'PK\x05\x06')
    _require(offset >= 0 and len(tail) - offset >= 22, 'Missing ZIP directory')
    _, disk, central_disk, on_disk, entries, central_bytes, central_offset, comment = struct.unpack_from('<4s4H2IH', tail, offset)
    _require(disk == central_disk == 0 and on_disk == entries and 1 <= entries <= MAX_FILES + 1 and
             central_bytes <= MAX_CENTRAL_BYTES and central_offset + central_bytes == size - len(tail) + offset and
             len(tail) - offset == 22 + comment, 'Unsupported/oversized ZIP directory (ZIP64/multidisk forbidden)')
    stream.seek(0)


def _extract(archive, stage):
    _regular(archive, MAX_ARCHIVE_BYTES)
    try:
        with archive.open('rb') as stream:
            _preflight(stream)
            with zipfile.ZipFile(stream) as source:
                infos = source.infolist()
                _require(len(infos) <= MAX_FILES + 1, 'ZIP entry count limit exceeded')
                seen = set()
                total = 0
                for info in infos:
                    _require(info.filename == info.orig_filename and info.filename not in seen,
                             'Duplicate/truncated ZIP member')
                    seen.add(info.filename)
                    mode = info.external_attr >> 16
                    _require(not info.is_dir() and stat.S_IFMT(mode) in (0, stat.S_IFREG) and
                             not (info.external_attr & 0x10) and not (info.flag_bits & 1), 'Non-regular/encrypted ZIP member')
                    _require(info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), 'Unsupported ZIP compression')
                    limit = MAX_MANIFEST_BYTES if info.filename == 'manifest.json' else MAX_FILE_BYTES
                    _require(0 <= info.file_size <= limit and info.file_size <= MAX_RATIO * max(1, info.compress_size),
                             'ZIP bomb/file size limit exceeded')
                    total += info.file_size
                    _require(total <= MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES, 'ZIP total size limit exceeded')
                _require('manifest.json' in seen, 'Missing manifest')
                _unique_names(name for name in seen if name != 'manifest.json')
                manifest = _json(source.read('manifest.json'))
                _require(isinstance(manifest, dict) and manifest.get('format') == FORMAT and
                         type(manifest.get('version')) is int and manifest['version'] == VERSION and
                         manifest.get('database') == DATABASE and isinstance(manifest.get('consistency'), dict),
                         'Unsupported backup format/version')
                entries = manifest.get('files')
                _require(isinstance(entries, list) and 1 <= len(entries) <= MAX_FILES, 'Invalid manifest files')
                names = []
                for entry in entries:
                    _require(isinstance(entry, dict) and set(entry) == {'path', 'bytes', 'sha256'}, 'Invalid manifest entry')
                    _name(entry['path'])
                    _require(type(entry['bytes']) is int and 0 <= entry['bytes'] <= MAX_FILE_BYTES and
                             isinstance(entry['sha256'], str) and _SHA.fullmatch(entry['sha256']), 'Invalid file receipt')
                    names.append(entry['path'])
                _unique_names(names)
                _require(set(names) == seen - {'manifest.json'} and DATABASE in names, 'Manifest/ZIP inventory mismatch')
                _require(sum(entry['bytes'] for entry in entries) <= MAX_TOTAL_BYTES, 'Total bytes limit exceeded')
                (stage / '.agent-runs').mkdir()
                for entry in entries:
                    info = source.getinfo(entry['path'])
                    _require(info.file_size == entry['bytes'], 'ZIP/manifest length mismatch')
                    path = stage / entry['path']
                    path.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    count = 0
                    with source.open(info) as incoming, path.open('xb') as outgoing:
                        while chunk := incoming.read(CHUNK):
                            count += len(chunk)
                            _require(count <= entry['bytes'], 'Expanded ZIP exceeds receipt')
                            digest.update(chunk)
                            outgoing.write(chunk)
                    _require(count == entry['bytes'] and digest.hexdigest() == entry['sha256'], 'File SHA256/bytes mismatch')
        _validate(stage, manifest)
        return manifest
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, EOFError, struct.error) as error:
        raise BackupError('Invalid ZIP: ' + str(error)) from error


def inspect_backup(archive):
    """Fully verify ZIP hashes, SQLite, version receipt and artifact links.

    Uses a private bounded temporary extraction, without opening project paths.
    Fixed limits: 20,000 files, 256 MiB/file (including DB), 2 GiB total,
    8 MiB manifest, depth 32 and expansion ratio 200. ZIP64 is not supported.
    """
    archive = _path(archive)
    with tempfile.TemporaryDirectory(prefix='kaibain-inspect-') as temporary:
        # Private mkdtemp folder: resolve system links such as macOS /var -> /private/var.
        return _extract(archive, Path(os.path.realpath(temporary)))


def restore_backup(archive, destination_db):
    """Verify in staging and atomically publish a NEW database directory.

    No Store initialization/migrations, processes, restored runtime configuration,
    auto-resume, acceptance, or Apply. Original project paths are unchanged data.
    Neither the destination directory nor any DB/WAL/artifact path may exist.
    A crash after publication can leave a complete restore, never half a pair.
    """
    archive, destination = _path(archive), _path(destination_db)
    parent = destination.parent
    _require(destination.name not in ('.agent-runs', '.', '..') and
             not os.path.lexists(parent) and parent.parent.is_dir(),
             'Restore requires a new destination directory with an existing parent')
    _require(not _private(destination.name) and not _DEVICE.match(destination.name) and
             not destination.name.endswith(('.', ' ')) and ':' not in destination.name,
             'Invalid destination database name')
    with tempfile.TemporaryDirectory(prefix='.kaibain-restore-', dir=parent.parent) as temporary:
        stage = Path(temporary) / 'payload'
        stage.mkdir()
        manifest = _extract(archive, stage)
        if destination.name != DATABASE:
            (stage / DATABASE).rename(stage / destination.name)
        for path in stage.rglob('*'):
            if path.is_file():
                with path.open('r+b') as stream:
                    os.fsync(stream.fileno())
        _rename_new(stage, parent)
    return manifest
