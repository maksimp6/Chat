"""Real C fixture allocation and cleanup on injected Python auth failure."""
import ctypes
from contextlib import ExitStack
from pathlib import Path
import shutil
import subprocess
import tempfile
import pytest

ROOT=Path(__file__).resolve().parents[2]
DRIVER=ROOT/'alice_mouse/drivers/keyboard'
FIXTURE=Path(__file__).with_name('keyboard_fixture.c')


def test_real_c_fixture_freed_on_auth_failure():
    assert (DRIVER/'keyboard.c').is_file() and FIXTURE.is_file()
    cc=shutil.which('clang') or shutil.which('cc')
    assert cc
    with tempfile.TemporaryDirectory(prefix='alice-auth-fault-') as tmp:
        libpath=Path(tmp)/'fixture.so'
        subprocess.run([cc,'-shared','-fPIC','-std=gnu11','-pthread','-Wall',
            '-Wextra','-Werror','-I',str(DRIVER),str(DRIVER/'keyboard.c'),
            str(FIXTURE),'-o',str(libpath)],check=True,capture_output=True,
            text=True,timeout=30)
        lib=ctypes.CDLL(str(libpath))
        emit_type=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_void_p,ctypes.c_ushort,
                                  ctypes.c_ushort,ctypes.c_int)
        clock_type=ctypes.CFUNCTYPE(ctypes.c_int64,ctypes.c_void_p)
        @emit_type
        def emit(*_):return 0
        @clock_type
        def clock(*_):return 0
        lib.alice_fixture_new.argtypes=[ctypes.c_void_p,emit_type,clock_type]
        lib.alice_fixture_new.restype=ctypes.c_void_p
        lib.alice_fixture_close.argtypes=[ctypes.c_void_p]
        lib.alice_fixture_close.restype=ctypes.c_int
        lib.alice_fixture_free.argtypes=[ctypes.c_void_p]
        lib.alice_fixture_free.restype=None
        for name in ('alice_fixture_live_count','alice_fixture_close_count','alice_fixture_free_count'):
            getattr(lib,name).restype=ctypes.c_int
        keyboard=lib.alice_fixture_new(None,emit,clock)
        assert keyboard
        with pytest.raises(RuntimeError,match='authorization rejected'):
            with ExitStack() as stack:
                stack.callback(lib.alice_fixture_free,keyboard)
                stack.callback(lib.alice_fixture_close,keyboard)
                assert lib.alice_fixture_live_count()==1
                raise RuntimeError('authorization rejected')
        assert lib.alice_fixture_live_count()==0
        assert lib.alice_fixture_close_count()==1
        assert lib.alice_fixture_free_count()==1
