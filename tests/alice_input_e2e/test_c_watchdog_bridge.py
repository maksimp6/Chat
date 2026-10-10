"""Actual C keyboard runner releases held keys without a client tick.

No root, uinput or Android event injection. Requires Keyboard #1091.
"""
import ctypes
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DRIVER=ROOT/'alice_mouse/drivers/keyboard/keyboard.c'
HEADER=DRIVER.with_name('keyboard.h')


def test_c_runner_releases_key_after_client_stall():
    assert DRIVER.is_file() and HEADER.is_file(), 'Keyboard #1091 must be integrated'
    cc=shutil.which('clang') or shutil.which('cc')
    assert cc
    with tempfile.TemporaryDirectory(prefix='alice-watchdog-') as tmp:
        source=Path(tmp)/'harness.c'
        executable=Path(tmp)/'harness'
        source.write_text(r'''
#include "keyboard.h"
#include <stdio.h>
#include <time.h>
#include <stdatomic.h>
static atomic_int ups=0;
static int emit(void *ctx,unsigned short type,unsigned short code,int value){
 (void)ctx;
 if(type==EV_KEY && code==KEY_A && value==0)atomic_fetch_add(&ups,1);
 return 0;
}
static int64_t now(void *ctx){
 (void)ctx;
 struct timespec t;
 clock_gettime(CLOCK_MONOTONIC,&t);
 return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000;
}
int main(void){
 alice_keyboard kb;
 alice_keyboard_runner runner;
 if(alice_keyboard_init(&kb,NULL,emit,now))return 10;
 if(alice_keyboard_runner_start(&runner,&kb))return 11;
 if(alice_keyboard_runner_down(&runner,KEY_A))return 12;
 /* The command client stops here. No tick() and no key_up() call. */
 struct timespec pause={.tv_sec=1,.tv_nsec=850000000};
 nanosleep(&pause,NULL);
 int observed=atomic_load(&ups);
 int result=alice_keyboard_runner_stop(&runner);
 if(result || observed!=1 || kb.held_count!=0)return 13;
 puts("C_WATCHDOG_KEY_UP_PASS");
 return 0;
}
''',encoding='utf-8')
        subprocess.run([cc,'-std=gnu11','-pthread','-Wall','-Wextra','-Werror',
            '-I',str(HEADER.parent),str(DRIVER),str(source),'-o',str(executable)],
            check=True,capture_output=True,text=True,timeout=30)
        result=subprocess.run([str(executable)],capture_output=True,text=True,
            timeout=6,check=True)
        assert result.stdout.strip()=='C_WATCHDOG_KEY_UP_PASS'
