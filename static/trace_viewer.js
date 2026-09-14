/* Alice Pro Execution Trace viewer. Self-contained and lazy-rendered. */
(function () {
    "use strict";

    var state = { trace: null, selected: null, modal: null, body: null };
    var ESC = String.fromCharCode(27);

    function el(tag, attrs, text) {
        var node = document.createElement(tag);
        attrs = attrs || {};
        Object.keys(attrs).forEach(function (key) {
            if (key === "className") node.className = attrs[key];
            else if (key === "title") node.title = attrs[key];
            else if (key === "textContent") node.textContent = attrs[key];
            else node.setAttribute(key, attrs[key]);
        });
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function fmtMs(value) {
        var n = Number(value);
        if (!Number.isFinite(n)) return "—";
        if (n < 1000) return Math.round(n) + " ms";
        return (n / 1000).toFixed(n < 10000 ? 2 : 1) + " s";
    }

    function fmtNumber(value) {
        var n = Number(value);
        return Number.isFinite(n) ? n.toLocaleString("ru-RU") : "0";
    }

    function jsonText(value) {
        if (value === undefined) return "undefined";
        if (typeof value === "string") return value;
        try { return JSON.stringify(value, null, 2); }
        catch (_) { return String(value); }
    }

    function normalizeTrace(input) {
        var trace = input;
        if (typeof trace === "string") {
            try { trace = JSON.parse(trace); } catch (_) { return null; }
        }
        if (!trace || typeof trace !== "object") return null;
        return trace;
    }

    function injectStyles() {
        if (document.getElementById("alice-trace-viewer-style")) return;
        var style = document.createElement("style");
        style.id = "alice-trace-viewer-style";
        style.textContent = `
.alice-trace-modal{position:fixed;inset:0;z-index:200000;background:rgba(0,0,0,.62);backdrop-filter:blur(8px);display:flex;align-items:stretch;justify-content:center;padding:18px;box-sizing:border-box}
.alice-trace-window{width:min(1500px,100%);height:100%;background:var(--bg-main,#111);color:var(--text-main,#eee);border:1px solid var(--border-color,rgba(255,255,255,.12));border-radius:14px;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 24px 80px rgba(0,0,0,.42);font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.alice-trace-header{display:flex;gap:12px;align-items:center;padding:12px 14px;border-bottom:1px solid var(--border-color,rgba(255,255,255,.1));background:rgba(127,127,127,.06)}
.alice-trace-title{font-weight:750;min-width:0}.alice-trace-sub{font:11px ui-monospace,SFMono-Regular,Menlo,monospace;opacity:.62;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.alice-trace-spacer{flex:1}.alice-trace-btn{border:1px solid var(--border-color,rgba(255,255,255,.14));background:rgba(127,127,127,.08);color:inherit;border-radius:8px;padding:7px 10px;cursor:pointer}.alice-trace-btn:hover{background:rgba(127,127,127,.16)}
.alice-trace-metrics{display:flex;flex-wrap:wrap;gap:7px;padding:8px 12px;border-bottom:1px solid var(--border-color,rgba(255,255,255,.08));font-size:11px}
.alice-trace-metric{padding:5px 8px;border-radius:7px;background:rgba(127,127,127,.07);white-space:nowrap}.alice-trace-metric b{font-weight:700}
.alice-trace-waterfall{padding:10px 12px 12px;border-bottom:1px solid var(--border-color,rgba(255,255,255,.08));overflow:auto}
.alice-trace-waterfall-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:7px;font-size:11px;font-weight:750;letter-spacing:.04em;text-transform:uppercase;opacity:.72}
.alice-trace-track{position:relative;min-width:620px;height:132px;padding-left:122px;box-sizing:border-box}.alice-trace-row{height:26px;position:relative;display:flex;align-items:center}
.alice-trace-row-label{position:absolute;left:-118px;width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px}.alice-trace-axis{position:absolute;left:122px;right:0;top:0;bottom:0;background:repeating-linear-gradient(to right,rgba(127,127,127,.12) 0,rgba(127,127,127,.12) 1px,transparent 1px,transparent 25%)}
.alice-trace-bar{position:absolute;height:14px;top:6px;border-radius:5px;min-width:3px;background:var(--accent,#7aa2ff);opacity:.9;cursor:pointer}.alice-trace-bar.tool{background:var(--accent,#7aa2ff)}.alice-trace-bar.response{background:#8b7dff}.alice-trace-point{position:absolute;top:7px;width:8px;height:8px;border-radius:50%;background:var(--text-main,#eee);transform:translateX(-4px);cursor:pointer}
.alice-trace-main{display:flex;min-height:0;flex:1}.alice-trace-sidebar{width:260px;min-width:210px;border-right:1px solid var(--border-color,rgba(255,255,255,.08));overflow:auto;padding:8px}.alice-trace-inspector{min-width:0;flex:1;overflow:auto}
.alice-trace-section-title{font-size:10px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;opacity:.55;padding:7px 8px}.alice-trace-item{width:100%;box-sizing:border-box;text-align:left;border:0;background:transparent;color:inherit;padding:8px;border-radius:8px;cursor:pointer;display:flex;gap:8px;align-items:flex-start}.alice-trace-item:hover,.alice-trace-item.active{background:rgba(127,127,127,.1)}.alice-trace-icon{width:18px;flex:0 0 18px;text-align:center}.alice-trace-item-main{min-width:0}.alice-trace-item-name{font-size:12px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.alice-trace-item-meta{font-size:10px;opacity:.55;margin-top:2px}
.alice-trace-inspector-inner{padding:14px 16px}.alice-trace-inspector-title{font-size:18px;font-weight:750;margin-bottom:3px}.alice-trace-inspector-sub{font-size:11px;opacity:.58;margin-bottom:12px}.alice-trace-tabs{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px}.alice-trace-tab{border:1px solid var(--border-color,rgba(255,255,255,.12));background:transparent;color:inherit;border-radius:7px;padding:6px 8px;font-size:11px;cursor:pointer}.alice-trace-tab.active{background:rgba(127,127,127,.12);font-weight:700}
.alice-trace-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px;margin-bottom:12px}.alice-trace-card{padding:9px;border:1px solid var(--border-color,rgba(255,255,255,.08));border-radius:8px;background:rgba(127,127,127,.035)}.alice-trace-card-label{font-size:10px;opacity:.55}.alice-trace-card-value{font-size:13px;font-weight:700;margin-top:2px;word-break:break-word}
.alice-trace-pre{margin:0;padding:12px;border-radius:9px;background:rgba(0,0,0,.18);border:1px solid var(--border-color,rgba(255,255,255,.07));font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;overflow:auto;max-height:calc(100vh - 320px);text-align:left}
.alice-trace-notice{padding:10px 12px;border-radius:8px;background:rgba(127,127,127,.06);font-size:12px;opacity:.75}
@media(max-width:820px){.alice-trace-modal{padding:0}.alice-trace-window{border-radius:0}.alice-trace-sidebar{width:180px;min-width:160px}.alice-trace-header{padding:9px}.alice-trace-waterfall{padding-left:8px;padding-right:8px}.alice-trace-main{height:calc(100vh - 300px)}}
@media(max-width:600px){.alice-trace-sidebar{width:145px;min-width:130px}.alice-trace-inspector-inner{padding:12px}.alice-trace-metrics{gap:5px}.alice-trace-metric{font-size:10px}}
`;
        document.head.appendChild(style);
    }

    function getTimestamp(obj) {
        if (!obj || typeof obj !== "object") return null;
        var candidates = [obj.timestamp, obj.end_timestamp, obj.created_at];
        for (var i = 0; i < candidates.length; i++) {
            var n = Number(candidates[i]);
            if (Number.isFinite(n)) return n;
            if (typeof candidates[i] === "string") {
                var parsed = Date.parse(candidates[i]);
                if (Number.isFinite(parsed)) return parsed / 1000;
            }
        }
        return null;
    }

    function buildItems(trace) {
        var items = [];
        (trace.events || []).forEach(function (ev, i) {
            items.push({kind:"event", index:i, name:ev.type || "event", timestamp:getTimestamp(ev), data:ev});
        });
        (trace.tool_calls || []).forEach(function (tool, i) {
            items.push({kind:"tool", index:i, name:tool.name || "tool", timestamp:getTimestamp(tool), start:Number(tool.start_timestamp), end:Number(tool.end_timestamp), data:tool});
        });
        (trace.responses || []).forEach(function (resp, i) {
            items.push({kind:"response", index:i, name:"Responses API #" + (resp.step || i + 1), timestamp:getTimestamp(resp), data:resp});
        });
        items.sort(function(a,b){ return (a.timestamp || Infinity) - (b.timestamp || Infinity); });
        return items;
    }

    function duration(trace) {
        if (trace.timings && Number.isFinite(Number(trace.timings.total_duration_ms))) return Number(trace.timings.total_duration_ms);
        var start = Number(trace.created_at), end = null;
        (trace.events || []).forEach(function(ev){ var t=getTimestamp(ev); if(t!==null) end=end===null?t:Math.max(end,t); });
        if (Number.isFinite(start) && end !== null) return Math.max(0,(end-start)*1000);
        return null;
    }

    function collectUsage(trace) {
        var result = {input:0, output:0, total:0};
        (trace.responses || []).forEach(function(r){
            var u = r.raw && r.raw.usage;
            if (!u) return;
            result.input += Number(u.input_tokens || 0);
            result.output += Number(u.output_tokens || 0);
            result.total += Number(u.total_tokens || 0);
        });
        return result;
    }

    function renderMetricRow(trace) {
        var row = el("div", {className:"alice-trace-metrics"});
        var usage = collectUsage(trace);
        var tools = Array.isArray(trace.tool_calls) ? trace.tool_calls.length : 0;
        var responses = Array.isArray(trace.responses) ? trace.responses.length : 0;
        var events = Array.isArray(trace.events) ? trace.events.length : 0;
        var errors = Array.isArray(trace.errors) ? trace.errors.length : 0;
        var metrics = [
            ["Duration", fmtMs(duration(trace))], ["Events", events], ["Responses", responses], ["Tools", tools],
            ["Tokens", fmtNumber(usage.total)], ["Cost", trace.cost != null ? ("≈ " + trace.cost + " ₽") : "—"],
            ["Status", errors ? ("⚠ " + errors) : "● completed"]
        ];
        metrics.forEach(function(m){ var card=el("div",{className:"alice-trace-metric"}); card.innerHTML = m[0] + ": <b></b>"; card.querySelector("b").textContent = String(m[1]); row.appendChild(card); });
        return row;
    }

    function renderWaterfall(trace, items) {
        var wrap = el("div", {className:"alice-trace-waterfall"});
        var head = el("div", {className:"alice-trace-waterfall-head"});
        head.appendChild(el("span",{},"Waterfall"));
        head.appendChild(el("span",{},"реальные интервалы tools · шаги Responses восстановлены по trace order"));
        wrap.appendChild(head);
        if (!items.length) { wrap.appendChild(el("div",{className:"alice-trace-notice"},"Trace не содержит временных событий.")); return wrap; }
        var times = [];
        items.forEach(function(it){ if(Number.isFinite(it.timestamp)) times.push(it.timestamp); if(Number.isFinite(it.start)) times.push(it.start); if(Number.isFinite(it.end)) times.push(it.end); });
        if (Number.isFinite(Number(trace.created_at))) times.push(Number(trace.created_at));
        var min = Math.min.apply(Math,times), max = Math.max.apply(Math,times), span = Math.max(0.001,max-min);
        var track = el("div",{className:"alice-trace-track"});
        track.appendChild(el("div",{className:"alice-trace-axis"}));
        var rows = [];
        var rowMap = {};
        function addRow(label) { var r=el("div",{className:"alice-trace-row"}); r.appendChild(el("div",{className:"alice-trace-row-label"},label)); track.appendChild(r); return r; }
        items.forEach(function(it){
            var key = it.kind + ":" + (it.index || 0);
            if (!rowMap[it.kind]) rowMap[it.kind] = addRow(it.kind === "tool" ? "Tools" : it.kind === "response" ? "Responses API" : "Events");
            var row=rowMap[it.kind];
            if (it.kind === "tool" && Number.isFinite(it.start) && Number.isFinite(it.end)) {
                var left=((it.start-min)/span)*100, width=Math.max(.35,((it.end-it.start)/span)*100);
                var bar=el("div",{className:"alice-trace-bar tool",title:it.name}); bar.style.left=left+"%"; bar.style.width=width+"%"; bar.onclick=(function(item){return function(){selectItem(item);};})(it); row.appendChild(bar);
            } else if (Number.isFinite(it.timestamp)) {
                var point=el("div",{className:"alice-trace-point",title:it.name}); point.style.left=((it.timestamp-min)/span)*100+"%"; point.onclick=(function(item){return function(){selectItem(item);};})(it); row.appendChild(point);
            }
        });
        wrap.appendChild(track); return wrap;
    }

    function renderSidebar(trace, items) {
        var sidebar = el("div",{className:"alice-trace-sidebar"});
        sidebar.appendChild(el("div",{className:"alice-trace-section-title"},"EVENTS / TRACE"));
        items.forEach(function(item){
            var btn=el("button",{className:"alice-trace-item"});
            var icon=item.kind==="tool"?"🔧":item.kind==="response"?"🤖":"•";
            btn.appendChild(el("span",{className:"alice-trace-icon"},icon));
            var main=el("span",{className:"alice-trace-item-main"});
            main.appendChild(el("span",{className:"alice-trace-item-name"},item.name));
            var meta=(item.kind === "tool" && Number.isFinite(item.start) && Number.isFinite(item.end)) ? fmtMs((item.end-item.start)*1000) : (Number.isFinite(item.timestamp) ? new Date(item.timestamp*1000).toLocaleTimeString("ru-RU") : "");
            main.appendChild(el("span",{className:"alice-trace-item-meta"},meta)); btn.appendChild(main);
            btn.onclick=function(){selectItem(item);}; item._button=btn; sidebar.appendChild(btn);
        });
        return sidebar;
    }

    function tabsFor(item) {
        if (item.kind === "response") return ["Overview","Request","Raw Response","Output","Usage","Tools","Reasoning","Metadata"];
        if (item.kind === "tool") return ["Overview","Arguments","Result","Metadata","Raw"];
        return ["Overview","Payload","Raw"];
    }

    function rawFor(item){ return item.kind === "response" ? (item.data.raw || item.data) : item.data; }

    function renderInspector(trace, item, tab) {
        var area = state.body.querySelector(".alice-trace-inspector");
        area.textContent = "";
        var inner = el("div",{className:"alice-trace-inspector-inner"});
        if (!item) { inner.appendChild(el("div",{className:"alice-trace-notice"},"Выберите событие слева, чтобы открыть inspector.")); area.appendChild(inner); return; }
        inner.appendChild(el("div",{className:"alice-trace-inspector-title"},item.name));
        inner.appendChild(el("div",{className:"alice-trace-inspector-sub"},item.kind.toUpperCase() + (item.data.step ? " · step " + item.data.step : "")));
        var tabs=el("div",{className:"alice-trace-tabs"});
        var selectedTab=tab || tabsFor(item)[0];
        tabsFor(item).forEach(function(t){ var b=el("button",{className:"alice-trace-tab"},t); if(t===selectedTab)b.classList.add("active"); b.onclick=function(){renderInspector(trace,item,t);}; tabs.appendChild(b); });
        inner.appendChild(tabs);
        var content=document.createElement("div");
        function pre(value){ content.appendChild(el("pre",{className:"alice-trace-pre"},jsonText(value))); }
        if (selectedTab==="Overview") {
            var grid=el("div",{className:"alice-trace-grid"});
            var pairs=[];
            if(item.kind==="tool"){ pairs=[["Name",item.data.name],["Server",item.data.server || "Local Registry"],["Duration",fmtMs(item.data.timing_ms)], ["Status",item.data.error?"error":"success"],["Call ID",item.data.call_id || "—"],["Step",item.data.step || "—"]]; }
            else if(item.kind==="response"){ pairs=[["Step",item.data.step || "—"],["Timestamp",item.data.timestamp ? new Date(item.data.timestamp*1000).toLocaleString("ru-RU") : "—"],["Model",item.data.raw && item.data.raw.model || "—"],["Response ID",item.data.raw && item.data.raw.id || "—"],["Output items",item.data.raw && Array.isArray(item.data.raw.output) ? item.data.raw.output.length : "—"]]; }
            else { pairs=[["Type",item.data.type || "event"],["Timestamp",item.data.timestamp ? new Date(item.data.timestamp*1000).toLocaleString("ru-RU") : "—"]]; }
            pairs.forEach(function(p){var c=el("div",{className:"alice-trace-card"}); c.appendChild(el("div",{className:"alice-trace-card-label"},p[0])); c.appendChild(el("div",{className:"alice-trace-card-value"},String(p[1])));grid.appendChild(c);}); content.appendChild(grid);
            if(item.kind==="tool" && item.data.error) content.appendChild(el("div",{className:"alice-trace-notice"},"⚠ " + String(item.data.error)));
            if(item.kind==="event") pre(item.data.payload !== undefined ? item.data.payload : item.data);
        } else if (selectedTab === "Request") pre(trace.request || {});
        else if (selectedTab === "Raw Response") pre(rawFor(item));
        else if (selectedTab === "Payload") pre(item.data.payload !== undefined ? item.data.payload : item.data);
        else if (selectedTab === "Raw") pre(rawFor(item));
        else if (selectedTab === "Arguments") pre(item.data.arguments || {});
        else if (selectedTab === "Result") pre(item.data.error ? {error:item.data.error,result:item.data.result} : item.data.result);
        else if (selectedTab === "Metadata") pre((item.kind === "response" ? (item.data.raw && item.data.raw.metadata) : {call_id:item.data.call_id,server:item.data.server,step:item.data.step,start_timestamp:item.data.start_timestamp,end_timestamp:item.data.end_timestamp}));
        else if (selectedTab === "Output") pre(item.data.raw && item.data.raw.output || []);
        else if (selectedTab === "Usage") pre(item.data.raw && item.data.raw.usage || {});
        else if (selectedTab === "Tools") pre(item.data.raw && item.data.raw.tools || trace.tool_calls || []);
        else if (selectedTab === "Reasoning") pre(item.data.raw && item.data.raw.reasoning || null);
        inner.appendChild(content); area.appendChild(inner);
    }

    function selectItem(item){
        state.selected=item; var buttons=state.modal.querySelectorAll(".alice-trace-item"); buttons.forEach(function(b){b.classList.remove("active")}); if(item._button)item._button.classList.add("active"); renderInspector(state.trace,item); }

    function copyTrace(){
        var text=JSON.stringify(state.trace,null,2);
        if(navigator.clipboard&&navigator.clipboard.writeText){ navigator.clipboard.writeText(text).catch(function(){ fallbackCopy(text); }); }
        else fallbackCopy(text);
    }
    function fallbackCopy(text){ var ta=document.createElement("textarea"); ta.value=text; document.body.appendChild(ta); ta.select(); try{document.execCommand("copy");}catch(_){} ta.remove(); }

    function close(){ if(state.modal){ state.modal.remove(); state.modal=null; state.trace=null; state.selected=null; } document.removeEventListener("keydown",onKey); }
    function onKey(e){ if(e.key===ESC) close(); }

    function open(input){
        var trace=normalizeTrace(input); if(!trace) return;
        injectStyles(); close(); state.trace=trace; var items=buildItems(trace);
        var modal=el("div",{className:"alice-trace-modal"}); var win=el("div",{className:"alice-trace-window",role:"dialog","aria-modal":"true","aria-label":"Execution Trace"});
        var header=el("div",{className:"alice-trace-header"}); header.appendChild(el("span",{},"⚡")); header.appendChild(el("div",{className:"alice-trace-title"},"Execution Trace")); header.appendChild(el("div",{className:"alice-trace-sub"},trace.trace_id ? String(trace.trace_id) : "local")); header.appendChild(el("div",{className:"alice-trace-spacer"})); var copy=el("button",{className:"alice-trace-btn",title:"Copy raw JSON"},"Copy JSON"); copy.onclick=copyTrace; header.appendChild(copy); var closeBtn=el("button",{className:"alice-trace-btn",title:"Close"},"×"); closeBtn.onclick=close; header.appendChild(closeBtn); win.appendChild(header);
        win.appendChild(renderMetricRow(trace)); win.appendChild(renderWaterfall(trace,items));
        var main=el("div",{className:"alice-trace-main"}); state.body=main; main.appendChild(renderSidebar(trace,items)); main.appendChild(el("div",{className:"alice-trace-inspector"})); win.appendChild(main); modal.appendChild(win); document.body.appendChild(modal); state.modal=modal; renderInspector(trace,null); document.addEventListener("keydown",onKey);
    }

    window.openTraceViewer = open;
})();
