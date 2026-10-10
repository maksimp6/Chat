"""Mocked Linux uinput teardown contract against the real C adapter.

No /dev/uinput is opened, no root privileges, no Android event injection.
Requires Keyboard #1091 in the combined checkout.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]
DRIVER=ROOT/'alice_mouse/drivers/keyboard'


def test_uinput_destroy_and_partial_write_fallback():
    required=['keyboard.c','keyboard.h','uinput_device.c','uinput_device.h']
    assert all((DRIVER/name).is_file() for name in required), 'Keyboard #1091 not integrated'
    cc=shutil.which('clang') or shutil.which('cc')
    assert cc
    source=r'''
#include "uinput_device.h"
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
typedef struct {
    int open_count,create_count,destroy_count,close_count,write_count,fail_up;
} mock;
static int op(void *ctx){mock *m=ctx;m->open_count++;return 31;}
static int ctl(void *ctx,int fd,unsigned long request,void *arg){
    mock *m=ctx;(void)arg;
    assert(fd==31);
    if(request==UI_DEV_CREATE)m->create_count++;
    if(request==UI_DEV_DESTROY)m->destroy_count++;
    return 0;
}
static ssize_t wr(void *ctx,int fd,const void *buffer,size_t length){
    mock *m=ctx;assert(fd==31);
    const struct input_event *event=buffer;
    m->write_count++;
    if(m->fail_up && event->type==EV_KEY && event->value==0)
        return (ssize_t)length-1;
    return (ssize_t)length;
}
static int cl(void *ctx,int fd){
    mock *m=ctx;assert(fd==31);m->close_count++;return 0;
}
static int64_t now(void *ctx){(void)ctx;return 0;}
int main(void){
    mock m={0};
    alice_uinput_ops ops={op,ctl,wr,cl,&m};
    alice_uinput_device dev={0};
    assert(!alice_uinput_create(&dev,&ops));
    assert(m.create_count==1);
    alice_uinput_emitter emitter={&dev,&ops};
    alice_keyboard kb;
    assert(!alice_keyboard_init(&kb,&emitter,alice_uinput_emit_bound,now));
    alice_keyboard_set_destroy(&kb,alice_uinput_destroy_bound);
    assert(!alice_keyboard_down(&kb,KEY_A));
    m.fail_up=1;
    assert(alice_keyboard_up(&kb,KEY_A)==-EIO);
    assert(kb.faulted && kb.destroyed);
    assert(m.destroy_count==1 && m.close_count==1);
    assert(!dev.created && !dev.owned);
    assert(alice_keyboard_down(&kb,KEY_B)==-EIO);
    assert(alice_uinput_emit_bound(&emitter,EV_KEY,KEY_B,1)==-EIO);
    assert(!alice_uinput_destroy(&dev,&ops));
    assert(m.destroy_count==1 && m.close_count==1);
    puts("MOCKED_UINPUT_TEARDOWN_PASS");
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='alice-uinput-teardown-') as tmp:
        code=Path(tmp)/'teardown.c'
        exe=Path(tmp)/'teardown'
        code.write_text(source,encoding='utf-8')
        subprocess.run([cc,'-std=gnu11','-pthread','-Wall','-Wextra','-Werror',
            '-I',str(DRIVER),str(DRIVER/'keyboard.c'),
            str(DRIVER/'uinput_device.c'),str(code),'-o',str(exe)],
            check=True,capture_output=True,text=True,timeout=30)
        result=subprocess.run([str(exe)],capture_output=True,text=True,
            timeout=10,check=True)
        assert result.stdout.strip()=='MOCKED_UINPUT_TEARDOWN_PASS'
