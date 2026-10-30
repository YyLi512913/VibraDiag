"""Owned process lifetime. Windows starts suspended and assigns kill-on-close before execution."""
import ctypes
from ctypes import wintypes
import os
import signal
import subprocess


class OwnedProcess:
    def __init__(self,arguments,environment,directory):
        self.closed=False;self.job=None
        if os.name!='nt':
            self.process=subprocess.Popen(arguments,env=environment,cwd=directory,stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            self.pid=self.process.pid;return
        import _winapi
        class IO(ctypes.Structure):
            _fields_=[(n,ctypes.c_ulonglong) for n in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class Basic(ctypes.Structure):
            _fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),
                ('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),
                ('ActiveProcessLimit',wintypes.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
        class Limits(ctypes.Structure):
            _fields_=[('BasicLimitInformation',Basic),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),
                ('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);self.kernel=kernel
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        kernel.TerminateJobObject.argtypes=[wintypes.HANDLE,wintypes.UINT]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.ResumeThread.argtypes=[wintypes.HANDLE];kernel.ResumeThread.restype=wintypes.DWORD
        self.job=kernel.CreateJobObjectW(None,None)
        if not self.job:raise OSError('Cannot create numerical job')
        limits=Limits();limits.BasicLimitInformation.LimitFlags=0x2000|0x200|0x8
        limits.BasicLimitInformation.ActiveProcessLimit=2  # venv launcher plus actual interpreter; no further descendants.
        limits.JobMemoryLimit=1024*1024*1024
        if not kernel.SetInformationJobObject(self.job,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            kernel.CloseHandle(self.job);self.job=None;raise OSError('Cannot constrain numerical job')
        handle=thread=None
        try:
            handle,thread,self.pid,_=_winapi.CreateProcess(arguments[0],subprocess.list2cmdline(arguments),None,None,False,
                0x4|0x08000000,environment,str(directory),subprocess.STARTUPINFO())
            self.handle=handle
            if not kernel.AssignProcessToJobObject(self.job,handle):raise OSError('Cannot attach numerical child')
            if kernel.ResumeThread(thread)==0xffffffff:raise OSError('Cannot start numerical child')
        except Exception:
            if handle is not None:_winapi.TerminateProcess(handle,1);_winapi.CloseHandle(handle)
            kernel.CloseHandle(self.job);self.job=None;raise
        finally:
            if thread is not None:_winapi.CloseHandle(thread)

    def poll(self):
        if os.name!='nt':return self.process.poll()
        import _winapi
        return None if _winapi.WaitForSingleObject(self.handle,0)==258 else _winapi.GetExitCodeProcess(self.handle)

    def kill(self):
        if os.name=='nt':self.kernel.TerminateJobObject(self.job,1)
        elif self.poll() is None:
            try:os.killpg(self.pid,signal.SIGKILL)
            except ProcessLookupError:pass

    def close(self):
        if self.closed:return
        self.closed=True
        if self.poll() is None:self.kill()
        if os.name=='nt':
            import _winapi
            _winapi.WaitForSingleObject(self.handle,5000)
            if self.job:self.kernel.CloseHandle(self.job)
            _winapi.CloseHandle(self.handle)
        else:self.process.wait(timeout=5)
