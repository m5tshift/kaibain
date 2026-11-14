import contextlib
import io
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from kaibain import launcher

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kaibain launch ')
        self.root = Path(self.temp.name).resolve()
        self.local = self.root / '.local'
        self.local.mkdir()
        self.root_patch = patch.object(launcher, 'ROOT', self.root)
        self.local_patch = patch.object(launcher, 'LOCAL', self.local)
        self.root_patch.start()
        self.local_patch.start()

    def tearDown(self):
        try:
            self.manage('stop')
        finally:
            self.local_patch.stop()
            self.root_patch.stop()
            self.temp.cleanup()

    def manage(self, mode, port=8765):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(launcher, 'prepare_environment', return_value=Path(sys.executable)):
            launcher.manage(mode, port, None)
        return json.loads(output.getvalue())

    def test_config_preserves_other_servers_and_uses_absolute_paths(self):
        target = self.root / '.mcp.json'
        other = {'command': 'other-server', 'args': []}
        target.write_text(json.dumps({'mcpServers': {'other': other}, 'custom': True}))
        database = self.root / 'data with spaces' / 'tasks.sqlite3'
        launcher.configure(Path(sys.executable), database)
        config = json.loads(target.read_text())
        self.assertEqual(config['mcpServers']['other'], other)
        self.assertTrue(config['custom'])
        self.assertEqual(config['mcpServers']['kaibain']['args'][-1], str(database))
        codex = tomllib.loads((self.local / 'codex.toml').read_text())
        self.assertEqual(codex['mcp_servers']['kaibain']['args'], config['mcpServers']['kaibain']['args'])
        # A previously generated entry can safely follow a changed DB setting.
        launcher.configure(Path(sys.executable), self.root / 'another.sqlite3')
        self.assertEqual(json.loads(target.read_text())['mcpServers']['other'], other)

    def test_different_existing_kaibain_is_not_overwritten(self):
        target = self.root / '.mcp.json'
        original = '{"mcpServers":{"kaibain":{"command":"someone-else"}}}'
        target.write_text(original)
        with self.assertRaisesRegex(RuntimeError, 'not overwritten'):
            launcher.configure(Path(sys.executable), self.root / 'data.sqlite3')
        self.assertEqual(target.read_text(), original)

    def test_background_start_reuse_health_stop_and_persistence(self):
        shutil.copy2(ROOT / 'run.py', self.root / 'run.py')
        shutil.copytree(ROOT / 'src', self.root / 'src', ignore=shutil.ignore_patterns('__pycache__'))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        started = self.manage('start', port)
        self.assertTrue(started['running'])
        self.assertFalse(started['reused'])
        record = json.loads((self.local/'runtime.json').read_text())
        self.assertTrue(launcher.owned_health(record))
        self.assertTrue(self.manage('status')['running'])
        reused = self.manage('start', port)
        self.assertTrue(reused['reused'])
        self.assertEqual(json.loads((self.local/'runtime.json').read_text())['pid'], record['pid'])
        from kaibain.store import Store
        store = Store(started['database'])
        task = store.create('Survives restart')
        self.assertFalse(self.manage('stop')['running'])
        self.assertFalse(self.manage('status')['running'])
        self.manage('start', port)
        self.assertEqual(Store(started['database']).get(task['id'])['title'], 'Survives restart')

    def test_setup_installs_only_when_requirements_change(self):
        environment = self.root / '.venv'
        python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        python.parent.mkdir(parents=True)
        python.touch()
        requirements = self.root / 'requirements.txt'
        requirements.write_text('mcp==1.30.0\n')
        with patch.object(launcher.subprocess, 'run') as run:
            run.return_value.returncode = 0
            launcher.prepare_environment()
            self.assertEqual(run.call_count, 2)  # probe + install
            run.reset_mock()
            launcher.prepare_environment()
            self.assertEqual(run.call_count, 1)  # probe only
            requirements.write_text('mcp==1.30.0\n# changed\n')
            run.reset_mock()
            launcher.prepare_environment()
            self.assertEqual(run.call_count, 2)


if __name__ == '__main__':
    unittest.main()
