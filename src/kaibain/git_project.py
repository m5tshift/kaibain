"""Resolve a trusted local project's source without hooks, network or a shell."""
import os
from pathlib import Path
import re
import subprocess
import tempfile

from .store import DomainError


def resolve_commit(repo, commit=None):
    if not Path(repo).is_dir():
        raise DomainError('PROJECT_UNAVAILABLE', 'Каталог репозитория недоступен. Проверьте путь в настройках проекта.', 409)
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    environment.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                        'GIT_NO_LAZY_FETCH': '1', 'GIT_NO_REPLACE_OBJECTS': '1',
                        'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': ''})
    argv = ['git', '--no-optional-locks', '-c', 'core.hooksPath='+os.devnull,
            '-c', 'core.fsmonitor=false', '-c', 'protocol.allow=never', '-c', 'credential.helper=',
            '-C', repo, 'rev-parse', '--verify', '--end-of-options', (commit or 'HEAD')+'^{commit}']
    try:
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                                       env=environment, **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
            try:
                code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise DomainError('PROJECT_GIT_TIMEOUT', 'Git не ответил за 5 секунд. Проверьте доступ к репозиторию.', 503) from None
            output.seek(0)
            value = output.read(4097).decode('ascii', errors='replace').strip()
    except FileNotFoundError:
        raise DomainError('GIT_UNAVAILABLE', 'Git не установлен или недоступен серверу в PATH.', 503) from None
    except OSError:
        raise DomainError('PROJECT_UNAVAILABLE', 'Не удалось прочитать Git-репозиторий.', 409) from None
    if code or not re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', value):
        raise DomainError('PROJECT_COMMIT_UNAVAILABLE', 'Исходный коммит недоступен. В репозитории должен быть хотя бы один коммит; закреплённый хеш должен существовать локально.', 409)
    return value.lower()
