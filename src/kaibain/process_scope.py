"""Own a trusted adapter's process tree, not a security/filesystem sandbox.

Windows: unnamed Job Object, no breakaway, kill-on-close. The bootstrap waits
for stdin so assignment precedes any adapter/MCP child creation. POSIX: a new
session/process group. Only the creating supervisor ever signals this group.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


class ProcessCleanupError(RuntimeError):
    pass


class WindowsJob:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.api = c.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            'SetInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            'AssignProcessToJobObject': ([w.HANDLE, w.HANDLE], w.BOOL),
            'TerminateJobObject': ([w.HANDLE, w.UINT], w.BOOL),
            'QueryInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'CloseHandle': ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name); function.argtypes = args; function.restype = result
        class Basic(c.Structure):
            _fields_ = [('PerProcessUserTimeLimit',c.c_longlong),('PerJobUserTimeLimit',c.c_longlong),
                        ('LimitFlags',w.DWORD),('MinimumWorkingSetSize',c.c_size_t),('MaximumWorkingSetSize',c.c_size_t),
                        ('ActiveProcessLimit',w.DWORD),('Affinity',c.c_size_t),('PriorityClass',w.DWORD),('SchedulingClass',w.DWORD)]
        class Extended(c.Structure):
            _fields_ = [('BasicLimitInformation',Basic),('IoInfo',c.c_ulonglong*6),
                        ('ProcessMemoryLimit',c.c_size_t),('JobMemoryLimit',c.c_size_t),
                        ('PeakProcessMemoryUsed',c.c_size_t),('PeakJobMemoryUsed',c.c_size_t)]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle: raise c.WinError(c.get_last_error())
        limits = Extended(); limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            error = c.WinError(c.get_last_error()); self.close(); raise error

    def assign(self, process):
        handle = self.api.OpenProcess(0x100 | 0x1, False, process.pid)  # SET_QUOTA | TERMINATE
        if not handle: raise self.c.WinError(self.c.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                raise self.c.WinError(self.c.get_last_error())
        finally:
            self.api.CloseHandle(handle)

    def terminate(self):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise self.c.WinError(self.c.get_last_error())
        # Basic accounting: four LARGE_INTEGERs, then four DWORDs; active is #3.
        class Accounting(self.c.Structure):
            _fields_ = [('times',self.c.c_longlong*4),('counts',self.c.c_ulong*4)]
        deadline = time.monotonic()+5
        while True:
            value = Accounting()
            if not self.api.QueryInformationJobObject(self.handle, 1, self.c.byref(value), self.c.sizeof(value), None):
                raise self.c.WinError(self.c.get_last_error())
            if value.counts[2] == 0: return
            if time.monotonic() >= deadline: raise RuntimeError('Не подтверждено завершение дерева процессов прогона.')
            time.sleep(.02)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle); self.handle = None


class ProcessScope:
    def __init__(self, argv, *, log, cwd, env, receipt=None):
        self.job = WindowsJob() if os.name == 'nt' else None
        self.process = None
        self.receipt=Path(receipt) if receipt else None
        self.run_id=env.get('KAIBAIN_RUN_ID')
        assigned = False
        try:
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
            self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('process_child.py'))],
                                            stdin=subprocess.PIPE, stdout=log, stderr=log, cwd=cwd, env=env, **options)
            if self.job: self.job.assign(self.process)
            assigned = True
            # Persist ownership before releasing the bootstrap gate. A new server
            # may inspect this receipt, but only this owner ever signals the scope.
            self._save_scope('active')
            self.process.stdin.write(json.dumps({'argv':argv}).encode('utf-8'))
            self.process.stdin.close()
        except BaseException:
            try:
                if self.process:
                    if assigned:
                        self.close()
                    else:
                        # Assignment failed before releasing the gate.
                        self.process.kill(); self.process.wait()
            finally:
                # A failed assignment never releases the gate. Closing its pipe
                # also releases a Windows venv interpreter behind the launcher.
                if self.process and self.process.stdin and not self.process.stdin.closed:self.process.stdin.close()
                if self.job: self.job.close()
            raise

    def _save_scope(self,status):
        if self.receipt:
            temporary=self.receipt.with_suffix('.tmp')
            temporary.write_text(json.dumps({'pid':self.process.pid,'platform':os.name,'run_id':self.run_id,'status':status}),encoding='utf-8')
            os.chmod(temporary,0o600);os.replace(temporary,self.receipt)

    def close(self):
        if not self.process: return
        try:
            if self.job:
                self.job.terminate()
            else:
                try: os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            self.process.wait(timeout=5)
            self._save_scope('closed')
        except Exception as error:
            raise ProcessCleanupError('Не удалось подтвердить остановку процессов. См. supervisor.log.') from error
        finally:
            if self.job: self.job.close()
            if self.process.stdin and not self.process.stdin.closed: self.process.stdin.close()
