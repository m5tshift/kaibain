"""Bounded, owned process for the trusted executor's read-only diagnostic protocol."""
import json
import os
from pathlib import Path
import tempfile
import time

from .process_scope import ProcessScope
from .store import ROOT, DomainError

PROBE_TIMEOUT_SECONDS=12


def item(identifier, label, status, message, hint='', **details):
    return {'id':identifier, 'label':label, 'status':status, 'message':message, 'hint':hint, 'details':details}


def probe(config, profile=None):
    if config.get('diagnostics') != 'readiness-v1':
        return [item('diagnostics', 'Диагностика исполнителя', 'failed', 'Адаптер не объявил поддержку диагностики.',
                     'Обновите multi-agent-system и переподключите встроенный исполнитель с сохранением его параметров.')]
    with tempfile.TemporaryDirectory(prefix='kaibain-readiness-') as temp:
        folder=Path(temp);os.chmod(folder,0o700)
        argv=[*config['command'],'--diagnose']
        if profile is not None:
            path=folder/'project.json';path.write_text(json.dumps(profile,ensure_ascii=False),encoding='utf-8');os.chmod(path,0o600)
            argv.extend(['--diagnostic-input',str(path)])
        environment={**os.environ,'PYTHONUTF8':'1','LANGSMITH_TRACING':'false','LANGCHAIN_TRACING_V2':'false'}
        for name in ('KAIBAIN_CONTROL_TOKEN','KAIBAIN_RUN_ID','KAIBAIN_CANCEL_FILE'):
            environment.pop(name,None)
        try:
            with (folder/'response.log').open('w+b') as output:
                scope=ProcessScope(argv,log=output,cwd=ROOT,env=environment)
                try:
                    deadline=time.monotonic()+PROBE_TIMEOUT_SECONDS
                    while scope.process.poll() is None:
                        if time.monotonic()>=deadline:
                            raise DomainError('READINESS_TIMEOUT','Исполнитель не завершил диагностику за 12 секунд.',503)
                        if output.seek(0,2)>65536:
                            raise ValueError('Output limit')
                        time.sleep(.025)
                    output.seek(0);raw=output.read(65537)
                    if scope.process.returncode or len(raw)>65536:
                        raise ValueError('Diagnostic adapter failed')
                finally:
                    scope.close()
            value=json.loads(raw)
            checks=value['checks']
            if (value.get('protocol')!='readiness-v1' or not isinstance(checks,list) or not 1<=len(checks)<=10 or
                    not all(isinstance(row,dict) and set(row)=={'id','label','status','message','hint','details'} and
                            all(isinstance(row[key],str) and len(row[key])<=2500 for key in ('id','label','status','message','hint')) and
                            row['status'] in ('passed','failed','skipped') and isinstance(row['details'],dict) for row in checks)):
                raise ValueError('Invalid diagnostics')
            required={'ollama','coordinator_model','developer_model','sandbox'}|({'project_files'} if profile else set())
            if any(arg=='--validator-model' or arg.startswith('--validator-model=') for arg in config['command'][2:]):
                required|={'validator_model','validator_family'}
            if any(arg=='--environment-bundle' or arg.startswith('--environment-bundle=') for arg in config['command'][2:]):
                required.add('dependency_environment')
            if any(arg in ('--guest-toolchain-bundle','--guest-toolchain-sha256') or
                    arg.startswith(('--guest-toolchain-bundle=','--guest-toolchain-sha256=')) for arg in config['command'][2:]):
                required.add('guest_toolchain')
            if any(arg.split('=',1)[0] in ('--guest-toolchain-bundle','--guest-toolchain-sha256','--environment-bundle','--environment-sha256')
                    for arg in config['command'][2:]):required.add('dependency_payload')
            ids=[row['id'] for row in checks]
            if len(ids)!=len(set(ids)) or not required<=set(ids):
                raise ValueError('Incomplete diagnostics')
            return checks
        except DomainError as error:
            return [item('diagnostics','Диагностика исполнителя','failed',error.message,'Повторите проверку; при повторении откройте настройки исполнителя.')]
        except Exception:
            return [item('diagnostics','Диагностика исполнителя','failed','Исполнитель не вернул корректный отчёт диагностики.',
                         'Проверьте зависимости и совместимость адаптера в multi-agent-system.')]
