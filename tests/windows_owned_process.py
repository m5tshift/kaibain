"""Windows fixture-only hard kill of a verified live child, never a saved PID alone."""
import ctypes as c
from ctypes import wintypes as w


def kill_owned_interpreter(launcher,pid):
    """Bind the interpreter to our live Popen launcher before opening a kill gate."""
    api=c.WinDLL('kernel32',use_last_error=True)
    signatures={
        'OpenProcess':([w.DWORD,w.BOOL,w.DWORD],w.HANDLE),
        'CreateToolhelp32Snapshot':([w.DWORD,w.DWORD],w.HANDLE),
        'Process32FirstW':([w.HANDLE,c.c_void_p],w.BOOL),
        'Process32NextW':([w.HANDLE,c.c_void_p],w.BOOL),
        'GetProcessTimes':([w.HANDLE,c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p],w.BOOL),
        'WaitForSingleObject':([w.HANDLE,w.DWORD],w.DWORD),
        'GetExitCodeProcess':([w.HANDLE,c.c_void_p],w.BOOL),
        'TerminateProcess':([w.HANDLE,w.UINT],w.BOOL),
        'CloseHandle':([w.HANDLE],w.BOOL),
    }
    for name,(args,result) in signatures.items():
        fn=getattr(api,name);fn.argtypes=args;fn.restype=result
    class Entry(c.Structure):
        _fields_=[('dwSize',w.DWORD),('cntUsage',w.DWORD),('pid',w.DWORD),
            ('heap',c.c_size_t),('module',w.DWORD),('threads',w.DWORD),
            ('parent',w.DWORD),('priority',w.LONG),('flags',w.DWORD),('exe',w.WCHAR*260)]
    if type(pid) is not int or pid<=1 or launcher.poll() is not None:
        raise ValueError('No live fixture launcher/valid interpreter')
    if pid==launcher.pid:
        launcher.kill();launcher.wait(timeout=5)
        return {'pid':pid,'launcher_pid':launcher.pid,'direct_popen':True,'exit_code':launcher.returncode}
    process=api.OpenProcess(0x1000|0x100000|0x1,False,pid)  # QUERY_LIMITED | SYNCHRONIZE | TERMINATE
    if not process:raise c.WinError(c.get_last_error())
    try:
        snapshot=api.CreateToolhelp32Snapshot(0x2,0)
        if snapshot==c.c_void_p(-1).value:raise c.WinError(c.get_last_error())
        try:
            entry=Entry();entry.dwSize=c.sizeof(entry);parent=None
            found=api.Process32FirstW(snapshot,c.byref(entry))
            while found:
                if entry.pid==pid:parent=entry.parent;break
                found=api.Process32NextW(snapshot,c.byref(entry))
        finally:api.CloseHandle(snapshot)
        def created(handle):
            values=[w.FILETIME() for _ in range(4)]
            if not api.GetProcessTimes(handle,*(c.byref(v) for v in values)):raise c.WinError(c.get_last_error())
            return values[0].dwLowDateTime+(values[0].dwHighDateTime<<32)
        if (parent!=launcher.pid or created(process)<created(int(launcher._handle)) or
                launcher.poll() is not None or api.WaitForSingleObject(process,0)!=258):
            raise ValueError('Interpreter is not the current live launcher child')
        # All checks use live handles, including creation ordering to prevent
        # accepting a child of a previous process that happened to reuse this PID.
        if not api.TerminateProcess(process,79):raise c.WinError(c.get_last_error())
        if api.WaitForSingleObject(process,5000)!=0:raise RuntimeError('Owned interpreter did not exit')
        code=w.DWORD()
        if not api.GetExitCodeProcess(process,c.byref(code)):raise c.WinError(c.get_last_error())
        launcher.wait(timeout=5)
        return {'pid':pid,'launcher_pid':launcher.pid,'parent_pid':parent,
            'creation_order_verified':True,'direct_popen':False,'exit_code':code.value}
    finally:api.CloseHandle(process)
