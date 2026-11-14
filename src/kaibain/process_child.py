"""Gate: do not execute the configured adapter before the parent owns its scope."""
import json
import os
import sys
import subprocess

if __name__ == '__main__':
    argv = json.load(sys.stdin)['argv']
    if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
        raise ValueError('Invalid executor argv')
    if os.name == 'nt':
        # Windows exec may exit the launcher before its replacement terminates.
        # Keep the job's gate alive until the configured adapter actually exits.
        raise SystemExit(subprocess.run(argv, stdin=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW).returncode)
    os.execvpe(argv[0], argv, os.environ)
