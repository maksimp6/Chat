#!/usr/bin/env python3
"""Record the installed Redmi 9 modal UI; never submit forms or generate replies."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

import redmi_screenshot_capture as native

CASES = [
    ("models", "#model-btn", "#model-modal"),
    ("system-status", "#system-status-btn", "#logos-system-status"),
    ("tools", "#tools-btn", "#tools-modal-custom"),
    ("ssh", "#ssh-runtime-btn", "#ssh-runtime-modal-custom"),
    ("mcp", "#mcp-btn", "#mcp-manager-modal"),
    ("mcp-editor", "#mcp-btn", "#mcp-manager-modal"),
    ("settings-gen", "#settings-btn", "#settings-modal-custom"),
    ("settings-output", "#settings-btn", "#settings-modal-custom"),
    ("settings-routing", "#settings-btn", "#settings-modal-custom"),
    ("settings-adv", "#settings-btn", "#settings-modal-custom"),
    ("settings-theme", "#settings-btn", "#settings-modal-custom"),
    ("files", "#file-manager-btn", "#file-manager-modal"),
    ("vector-add-files", "#file-manager-btn", "#vs-add-files-modal"),
    ("treasury", "#treasury-btn", "#treasury-modal"),
    ("project-tree", "#project-tree-btn", "#project-tree-modal"),
    ("memory", "#memory-btn", "#memoryModal"),
    ("departments", "#departments-btn", "#departments-modal"),
    ("partners", "#departments-btn", "#partner-relations-modal"),
    ("environments", "#environments-btn", "#environments-modal"),
    ("trace", None, ".logos-trace-window"),
    ("trace-event", None, ".logos-trace-window"),
]

INSTALL = r"""JSON.stringify((()=>{
  const style=document.createElement('style');style.id='modal-recording-private-fields';
  style.textContent=`
    input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]),textarea {
      color:transparent!important;text-shadow:none!important;caret-color:transparent!important;
      -webkit-text-fill-color:transparent!important;background:#52606d!important;
    }
    input::placeholder,textarea::placeholder{color:transparent!important;-webkit-text-fill-color:transparent!important;}
    pre,code,.logos-trace-pre,.logos-system-status-message,#cloudru-iam-output {
      color:transparent!important;text-shadow:none!important;background:#52606d!important;
    }
    pre *,code *,.logos-trace-pre *{color:transparent!important;text-shadow:none!important;}
  `;document.head.appendChild(style);
  const requests=[];window.__modalRecordingRequests=requests;
  const stamp=()=>({date_ms:Date.now(),performance_ms:performance.now()});
  const begin=(method,url)=>{const u=new URL(url,location.href);const r={method:String(method).toUpperCase(),
    path:u.origin===location.origin?u.pathname:'[external]',started:stamp(),finished:null,status:null,blocked:false};
    requests.push(r);return r;};
  const original=window.fetch;
  window.fetch=function(input,init){const method=(init&&init.method)||(input&&input.method)||'GET';
    const r=begin(method,typeof input==='string'?input:input.url);
    if(!['GET','HEAD'].includes(r.method)||r.path==='[external]'){
      r.blocked=true;r.finished=stamp();return Promise.reject(new Error('Modal recording is read-only'));}
    return original.apply(this,arguments).then(response=>{r.status=response.status;r.finished=stamp();return response;},
      error=>{r.finished=stamp();throw error;});};
  const open=XMLHttpRequest.prototype.open,send=XMLHttpRequest.prototype.send,meta=new WeakMap();
  XMLHttpRequest.prototype.open=function(method,url){meta.set(this,{method,url});return open.apply(this,arguments);};
  XMLHttpRequest.prototype.send=function(){const m=meta.get(this)||{method:'GET',url:location.href};
    const r=begin(m.method,m.url);if(!['GET','HEAD'].includes(r.method)||r.path==='[external]'){
      r.blocked=true;r.finished=stamp();throw new Error('Modal recording is read-only');}
    this.addEventListener('loadend',()=>{r.status=this.status;r.finished=stamp();},{once:true});return send.apply(this,arguments);};
  return {installed:true,read_only:true,redaction:'CSS masks before modal insertion; field values and response bodies are not read'};
})())"""


def capture(output, uid, gid, metadata):
    native.CAPTURE_DEADLINE = time.monotonic() + 480
    rdp, actor, preparation = native.prepare_browser(metadata)
    output.mkdir(parents=True, exist_ok=True)
    initial = rdp.state(actor)
    if initial['title'] != 'Logos' or initial['modalOpen']:
        raise RuntimeError('The initial application view is not clean')
    runtime = native.version()
    installed = rdp.evaluate(actor, INSTALL)
    files, artifacts, events, results, collected_requests = [], [], [], [], []
    recorder = None
    video_path = None
    part = 0
    recording_started = None

    def mark(name, data=None):
        value = {'event': name, 'wall_utc': datetime.now(timezone.utc).isoformat(),
                 'monotonic_ns': time.monotonic_ns(), 'video': video_path.name if video_path else None,
                 'recorder_running': recorder is not None and recorder.poll() is None}
        if data is not None:
            value['data'] = data
        events.append(value)

    def browser(js):
        return rdp.evaluate(actor, 'JSON.stringify((()=>{' + js + '})())')

    def visible(selector):
        return browser('const e=document.querySelector(' + json.dumps(selector) + ');'
                       'if(!e)return false;const r=e.getBoundingClientRect();'
                       'return !e.hidden&&getComputedStyle(e).visibility!=="hidden"&&r.width>0&&r.height>0;')

    def wait_for(selector, seconds=8):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if visible(selector):
                time.sleep(.55)
                return
            time.sleep(.2)
        raise RuntimeError('requested_modal_not_visible')

    def start_video():
        nonlocal recorder, video_path, part, recording_started
        part += 1
        video_path = output / ('all-modals-' + str(part).zfill(2) + '.mp4')
        mark('recorder_launch_requested')
        recorder = native.start_screenrecord(video_path, time_limit=180, bit_rate=1500000)
        if recorder is None:
            raise RuntimeError('native_screenrecord_unavailable')
        time.sleep(.6)
        if recorder.poll() is not None:
            raise RuntimeError('native_screenrecord_did_not_start')
        recording_started = time.monotonic()
        mark('recorder_started')

    def stop_video():
        nonlocal recorder
        if recorder is None:
            return
        mark('recorder_stop_requested')
        if recorder.poll() is not None:
            raise RuntimeError('video_ended_before_modal_walk_finished')
        video, artifact, failures = native.finish_screenrecord(recorder, video_path, uid, gid, 40*1024*1024)
        mark('recorder_stopped', video)
        recorder = None
        if artifact:
            artifacts.append(artifact)
        if failures:
            raise RuntimeError(','.join(failures))

    def snapshot(name, selector):
        native.device_guard()
        native.native_focus_is_logos()
        state = rdp.state(actor)
        if state['revision'] != initial['revision']:
            raise RuntimeError('application_revision_changed')
        if recorder is None or recorder.poll() is not None:
            raise RuntimeError('video_is_not_running')
        geometry = browser('const e=document.querySelector(' + json.dumps(selector) + ');'
                           'if(!e)return null;const r=e.getBoundingClientRect();'
                           'return {x:r.x,y:r.y,width:r.width,height:r.height,viewport:{width:innerWidth,height:innerHeight}};')
        mark('screencap_started', {'name': name, 'root': selector, 'geometry': geometry})
        png = native.command(native.ROOT+'screencap','-p')
        if png[:8] != b'\x89PNG\r\n\x1a\n':
            raise RuntimeError('native_screencap_invalid')
        path = output/name
        path.write_bytes(png)
        path.chmod(0o600)
        os.chown(path,uid,gid)
        entry = {'path':name,'sha256':hashlib.sha256(png).hexdigest(),'size':len(png),
                 'root':selector,'captured_at':datetime.now(timezone.utc).isoformat(),'redacted':True,'video':video_path.name}
        files.append(entry)
        mark('screencap_finished',entry)

    def close_all():
        # Start each window in a fresh document, so an unclosed predecessor cannot
        # obscure its header button. This records openings; it does not certify X buttons.
        nonlocal actor
        collected_requests.extend(browser('return window.__modalRecordingRequests||[];'))
        origin=rdp.state(actor)['time_origin_ms']
        mark('fresh_document_requested')
        browser('setTimeout(()=>location.reload(),100);return {scheduled:true};')
        deadline=time.monotonic()+18
        while time.monotonic()<deadline:
            try:
                candidate=rdp.find_logos()
                state=rdp.state(candidate)
                if state['time_origin_ms']!=origin and state['document_ready_state']=='complete':
                    if state['revision']!=initial['revision']:
                        raise native.CaptureSafetyError('application_revision_changed')
                    actor=candidate
                    rdp.evaluate(actor,INSTALL)
                    mark('fresh_document_ready')
                    return
            except native.CaptureSafetyError:
                raise
            except (OSError,RuntimeError,KeyError):
                pass
            time.sleep(.3)
        raise RuntimeError('fresh_document_did_not_load')

    def open_trace():
        selector = '.logos-trace-open-viewer,.trace-viewer-auto-button'
        browser('window.__modalTraceOpenErrors=[];window.addEventListener("error",e=>{'
                'window.__modalTraceOpenErrors.push({type:e.error&&e.error.name||"Error",'
                'message:String(e.message||"").replace(/https?:\\/\\/\\S+/g,"[url]").slice(0,180),'
                'file:String(e.filename||"").split("?")[0].split("/").pop(),line:e.lineno});});return {observing:true};')
        ready_until=time.monotonic()+3
        while time.monotonic()<ready_until and not browser('return !!document.querySelector('+json.dumps(selector)+');'):
            time.sleep(.2)
        if not browser('return !!document.querySelector(' + json.dumps(selector) + ');'):
            rdp.set_open(actor,True)
            candidates = rdp.chat_candidates(actor)
            for candidate in candidates[:8]:
                rdp.open_conversation(actor,candidate['index'])
                time.sleep(.6)
                if browser('return !!document.querySelector(' + json.dumps(selector) + ');'):
                    break
            rdp.set_open(actor,False)
        count=browser('return document.querySelectorAll('+json.dumps(selector)+').length;')
        for position in reversed(range(count)):
            browser('for(const x of document.querySelectorAll("[data-recording-trace]"))x.removeAttribute("data-recording-trace");'
                    'const b=document.querySelectorAll('+json.dumps(selector)+')['+str(position)+'];'
                    'if(!b)throw Error("no_existing_trace_button");'
                    'for(let p=b.parentElement;p;p=p.parentElement){if(p.tagName==="DETAILS")p.open=true;}'
                    'b.setAttribute("data-recording-trace","true");return {disclosures_open:true};')
            rdp.click(actor,'[data-recording-trace="true"]')
            time.sleep(.6)
            errors=browser('return window.__modalTraceOpenErrors;')
            mark('existing_trace_button_clicked',{'button_index':position,'button_count':count,'errors':errors})
            if visible('.logos-trace-window'):
                return
        raise RuntimeError('existing_trace_buttons_did_not_open_viewer')

    try:
        close_all()
        start_index = int(metadata.get('start_index',0))
        if not 0 <= start_index < len(CASES):
            raise RuntimeError('invalid_recording_start_index')
        if start_index>=19:
            start_video()
            snapshot('98-starting-view.png','body')
        for index, (name, opener, selector) in enumerate(CASES[start_index:],start=start_index):
            if recorder is None:
                start_video()
            elif time.monotonic()-recording_started > 90:
                stop_video()
                start_video()
            close_all()
            case = {'name':name,'root':selector,'status':'NOT_CAPTURED','video':video_path.name}
            mark('modal_open_requested',{'name':name,'opener':opener,'root':selector})
            try:
                native.device_guard()
                if name.startswith('trace'):
                    open_trace()
                else:
                    rdp.click(actor,opener)
                parent = '#file-manager-modal' if name=='vector-add-files' else '#departments-modal' if name=='partners' else selector
                wait_for(parent)
                if name.startswith('settings-'):
                    tab=name.removeprefix('settings-')
                    rdp.click(actor,'#settings-modal-custom .llm-tab-btn[data-tab="tab-'+tab+'"]')
                elif name=='mcp-editor':
                    rdp.click(actor,'#mcp-mgr-add')
                    wait_for('#mcp-ed-name')
                elif name=='vector-add-files':
                    wait_for('#file-manager-modal .vs-add-files-btn',seconds=6)
                    rdp.click(actor,'#file-manager-modal .vs-add-files-btn')
                elif name=='partners':
                    browser('const b=[...document.querySelectorAll(".logos-dept-row")].find(e=>/партн/i.test(e.textContent));'
                            'if(!b)throw Error("partner_department_missing");b.click();return {clicked:true};')
                elif name=='trace-event':
                    rdp.click(actor,'.logos-trace-mobile-list .logos-trace-item')
                wait_for(selector)
                mark('modal_visible',{'name':name,'root':selector})
                snapshot(str(index*2).zfill(2)+'-'+name+'-top.png',selector)
                scrolled = browser('const root=document.querySelector('+json.dumps(selector)+');let count=0;'
                                   'for(const e of [root,...root.querySelectorAll("*")]){const s=getComputedStyle(e);'
                                   'if(e.scrollHeight>e.clientHeight+12&&/(auto|scroll)/.test(s.overflowY)){'
                                   'e.scrollTop=e.scrollHeight;count++;}}return {containers:count};')
                time.sleep(.75)
                snapshot(str(index*2+1).zfill(2)+'-'+name+'-bottom.png',selector)
                case.update(status='CAPTURED',scroll_containers=scrolled['containers'])
                mark('modal_capture_finished',case)
            except (OSError,RuntimeError,KeyError) as error:
                case.update(status='NOT_CAPTURED',reason=str(error)[:120])
                mark('modal_capture_failed',case)
                try:
                    snapshot(str(index*2).zfill(2)+'-'+name+'-unavailable.png','body')
                except Exception:
                    pass
            results.append(case)
            close_all()
        stop_video()
    finally:
        if recorder is not None:
            try:
                stop_video()
            except Exception:
                if recorder is not None and recorder.poll() is None:
                    recorder.send_signal(signal.SIGINT)
        try:
            close_all()
        except Exception:
            pass
        requests = collected_requests + browser('return window.__modalRecordingRequests||[];')
        mark('walk_finished',{'captured':sum(x['status']=='CAPTURED' for x in results),'planned':len(CASES)})
        timeline=output/'events.json'
        timeline.write_text(json.dumps({'scenario':'all_application_modals','events':events,'network':requests},ensure_ascii=False,indent=2)+'\n')
        artifacts.append({'path':'events.json','size':timeline.stat().st_size,'sha256':hashlib.sha256(timeline.read_bytes()).hexdigest()})
        manifest={'schema_version':3,'device':'Redmi 9','browser':'Iceraven','scenario':'all_application_modals',
                  'runtime_revision':runtime['runtime_revision'],'browser_revision':initial['revision'],
                  'workflow_revision':metadata['workflow_revision'],'github_run_url':metadata['github_run_url'],
                  'captured_at':datetime.now(timezone.utc).isoformat(),'preparation':preparation,'guard':installed,
                  'files':files,'artifacts':artifacts,'modal_states':results,'planned_states':len(CASES)-start_index,
                  'inventory_total':len(CASES),'start_index':start_index,
                  'captured_states':sum(x['status']=='CAPTURED' for x in results),
                  'mutating_requests_transmitted':sum(x['method'] not in ('GET','HEAD') and not x['blocked'] for x in requests),
                  'limits':['DOM button clicks do not prove physical touch input','MP4 frame clock must be read for exact alignment',
                            'Each window is opened in a fresh document; close-button behavior is not certified by that isolation',
                            'Sensitive field values and raw trace text are masked; values are not validated',
                            'Cloud.ru IAM source is not loaded by the installed application template; no script was injected to fabricate that modal']}
        (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        for path in output.iterdir():
            path.chmod(0o600)
            os.chown(path,uid,gid)
        rdp.s.close()
        native.restore_clean_page()
    print(json.dumps({'captured_states':manifest['captured_states'],'planned_states':manifest['planned_states'],'videos':part,
                      'missing':[x['name'] for x in results if x['status']!='CAPTURED']},ensure_ascii=False))


def main():
    if len(sys.argv)==6 and sys.argv[1]=='_capture':
        capture(Path(sys.argv[2]),int(sys.argv[3]),int(sys.argv[4]),json.loads(sys.argv[5]))
        return
    required=['GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_SHA','RUNNER_NAME','RUNNER_TEMP']
    if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('CI')!='true' or any(not os.environ.get(k) for k in required):
        raise RuntimeError('Use the existing GitHub Actions runner')
    if len(sys.argv)!=2:
        raise RuntimeError('Usage: redmi_modal_capture.py OUTPUT_DIRECTORY')
    output=Path(sys.argv[1]).resolve()
    if Path(os.environ['RUNNER_TEMP']).resolve() not in output.parents:
        raise RuntimeError('Output must be inside RUNNER_TEMP')
    metadata={'github_run_id':os.environ['GITHUB_RUN_ID'],'github_run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],
              'workflow_revision':os.environ['GITHUB_SHA'],'runner_name':os.environ['RUNNER_NAME'],
              'start_index':int(os.environ.get('MODAL_RECORDING_START_INDEX','0')),
              'github_run_url':'https://github.com/maksimp6/Chat/actions/runs/'+os.environ['GITHUB_RUN_ID']}
    argv=[sys.executable,str(Path(__file__).resolve()),'_capture',str(output),str(os.getuid()),str(os.getgid()),json.dumps(metadata)]
    env={'PATH':os.environ.get('PATH','/system/bin'),'TMPDIR':os.environ.get('TMPDIR',str(output.parent))}
    result=subprocess.run(['su','-c',shlex.join(argv)],env=env,capture_output=True,timeout=510)
    print(result.stdout.decode(errors='replace').strip())
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace')[-1200:])


if __name__=='__main__':
    main()
