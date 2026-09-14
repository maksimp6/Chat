/* Alice Pro Execution Trace viewer. Responsive trace inspector with real-time waterfall. */
(function () {
    "use strict";

    var state = { trace: null, selected: null, modal: null, body: null, nav: null };
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
.alice-trace-header{display:flex;gap:10px;align-items:center;padding:12px 14px;border-bottom:1px solid var(--border-color,rgba(255,255,255,.1));background:rgba(127,127,127,.06)}
.alice-trace-title{font-weight:750;min-width:0}.alice-trace-sub{font:11px ui-monospace,SFMono-Regular,Menlo,monospace;opacity:.62;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.alice-trace-spacer{flex:1}
.alice-trace-btn{border:1px solid var(--border-color,rgba(255,255,255,.14));background:rgba(127,127,127,.08);color:inherit;border-radius:8px;padding:7px 10px;cursor:pointer}.alice-trace-btn:hover{background:rgba(127,127,127,.16)}
.alice-trace-metrics{display:flex;flex-wrap:wrap;gap:7px;padding:8px 12px;border-bottom:1px solid var(--border-color,rgba(255,255,255,.08));font-size:11px}
.alice-trace-metric{padding:5px 8px;border-radius:7px;background:rgba(127,127,127,.07);white-space:nowrap}.alice-trace-metric b{font-weight:700}
.alice-trace-waterfall{padding:10px 12px 12px;border-bottom:1px solid var(--border-color,rgba(255,255,255,.08));overflow-x:auto;overflow-y:hidden}
.alice-trace-waterfall-head{display:flex;gap:10px;justify-content:space-between;align-items:center;margin-bottom:8px;font-size:11px;font-weight:750;letter-spacing:.03em;text-transform:uppercase;opacity:.72}.alice-trace-waterfall-sub{font-size:10px;font-weight:500;text-transform:none;letter-spacing:0;white-space:nowrap}
.alice-trace-timeline{min-width:520px}.alice-trace-axis-labels{display:grid;grid-template-columns:108px 1fr;align-items:end;margin-bottom:4px;font:10px ui-monospace,SFMono-Regular,Menlo,monospace;opacity:.5}.alice-trace-axis-values{display:flex;justify-content:space-between;padding:0 2px}
.alice-trace-timeline-body{position:relative}.alice-trace-gridline{position:absolute;top:0;bottom:0;width:1px;background:rgba(127,127,127,.12);pointer-events:none}.alice-trace-timeline-row{display:grid;grid-template-columns:108px 1fr;min-height:27px;align-items:center}.alice-trace-row-label{padding-right:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px;opacity:.72}.alice-trace-row-track{position:relative;height:27px;border-bottom:1px solid rgba(127,127,127,.06)}
.alice-trace-bar{position:absolute;top:7px;height:13px;border-radius:5px;min-width:5px;background:var(--accent,#7aa2ff);opacity:.92;cursor:pointer;box-shadow:0 0 0 1px rgba(255,255,255,.04) inset}.alice-trace-bar.tool{background:var(--accent,#7aa2ff)}.alice-trace-bar.selected{outline:2px solid rgba(255,255,255,.75);outline-offset:1px}
.alice-trace-point{position:absolute;top:9px;width:9px;height:9px;border-radius:50%;background:var(--text-main,#eee);transform:translateX(-50%);cursor:pointer;box-shadow:0 0 0 2px rgba(127,127,127,.22)}.alice-trace-point.response{background:#a794ff}.alice-trace-point.event{background:#e7e7e7}.alice-trace-zero{position:absolute;left:0;right:0;top:0;bottom:0;display:flex;align-items:center;justify-content:center;font-size:10px;opacity:.45}
.alice-trace-main{display:flex;min-height:0;flex:1}.alice-trace-nav{width:280px;min-width:220px;border-right:1px solid var(--border-color,rgba(255,255,255,.08));overflow:auto;padding:8px}.alice-trace-inspector{min-width:0;flex:1;overflow:auto}
.alice-trace-section-title{font-size:10px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;opacity:.55;padding:7px 8px}.alice-trace-item{width:100%;box-sizing:border-box;text-align:left;border:0;background:transparent;color:inherit;padding:8px;border-radius:8px;cursor:pointer;display:flex;gap:8px;align-items:flex-start}.alice-trace-item:hover,.alice-trace-item.active{background:rgba(127,127,127,.1)}.alice-trace-icon{width:18px;flex:0 0 18px;text-align:center}.alice-trace-item-main{min-width:0}.alice-trace-item-name{font-size:12px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.alice-trace-item-meta{font-size:10px;opacity:.55;margin-top:2px}
.alice-trace-mobile-tabs{display:none}.alice-trace-inspector-inner{padding:14px 16px}.alice-trace-inspector-title{font-size:18px;font-weight:750;margin-bottom:3px}.alice-trace-inspector-sub{font-size:11px;opacity:.58;margin-bottom:12px}.alice-trace-tabs{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px}.alice-trace-tab{border:1px solid var(--border-color,rgba(255,255,255,.12));background:transparent;color:inherit;border-radius:7px;padding:6px 8px;font-size:11px;cursor:pointer}.alice-trace-tab.active{background:rgba(127,127,127,.12);font-weight:700}
.alice-trace-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px;margin-bottom:12px}.alice-trace-card{padding:9px;border:1px solid var(--border-color,rgba(255,255,255,.08));border-radius:8px;background:rgba(127,127,127,.035)}.alice-trace-card-label{font-size:10px;opacity:.55}.alice-trace-card-value{font-size:13px;font-weight:700;margin-top:2px;word-break:break-word}
.alice-trace-pre{margin:0;padding:12px;border-radius:9px;background:rgba(0,0,0,.18);border:1px solid var(--border-color,rgba(255,255,255,.07));font:11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre;overflow:auto;max-height:calc(100vh - 320px);text-align:left;tab-size:2}
.alice-trace-json-wrap{min-width:0;overflow:hidden}.alice-trace-notice{padding:10px 12px;border-radius:8px;background:rgba(127,127,127,.06);font-size:12px;opacity:.75}
@media(max-width:820px){.alice-trace-modal{padding:0}.alice-trace-window{border-radius:0}.alice-trace-nav{display:none}.alice-trace-mobile-tabs{display:flex;gap:5px;padding:7px 8px;border-bottom:1px solid var(--border-color,rgba(255,255,255,.08));overflow-x:auto}.alice-trace-mobile-tab{flex:0 0 auto;border:1px solid var(--border-color,rgba(255,255,255,.12));background:transparent;color:inherit;border-radius:7px;padding:6px 9px;font-size:11px;cursor:pointer}.alice-trace-mobile-tab.active{background:rgba(127,127,127,.12);font-weight:700}.alice-trace-main{min-height:0}.alice-trace-header{padding:9px}.alice-trace-waterfall{padding-left:8px;padding-right:8px}.alice-trace-inspector-inner{padding:12px}.alice-trace-timeline{min-width:460px}}
@media(max-width:600px){.alice-trace-title{font-size:13px}.alice-trace-sub{max-width:120px}.alice-trace-btn{padding:6px 8px}.alice-trace-metrics{gap:5px;padding:7px}.alice-trace-metric{font-size:10px;padding:4px 6px}.alice-trace-waterfall-head{align-items:flex-start;flex-direction:column;gap:3px}.alice-trace-timeline{min-width:430px}.alice-trace-axis-labels{grid-template-columns:88px 1fr}.alice-trace-timeline-row{grid-template-columns:88px 1fr}.alice-trace-row-label{font-size:9px}.alice-trace-pre{font-size:10px;line-height:1.45}.alice-trace-inspector-title{font-size:16px}}
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
            var start = Number(tool.start_timestamp);
            var end = Number(tool.end_timestamp);
            var hasSpan = Number.isFinite(start) && Number.isFinite(end) && end >= start;
            items.push({kind:"tool", index:i, name:tool.name || "tool", timestamp:hasSpan ? end : getTimestamp(tool), start:start, end:end, data:tool});
        });
        (trace.responses || []).forEach(function (resp, i) {
            items.push({kind:"response", index:i, name:"Responses API #" + (resp.step || i + 1), timestamp:getTimestamp(resp), data:resp});
        });
        items.sort(function(a,b){ return (a.timestamp == null ? Infinity : a.timestamp) - (b.timestamp == null ? Infinity : b.timestamp); });
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
        metrics.forEach(function(m){
            var card=el("div",{className:"alice-trace-metric"});
            card.appendChild(document.createTextNode(m[0] + ": "));
            card.appendChild(el("b",{},String(m[1])));
            row.appendChild(card);
        });
        return row;
    }

    function renderWaterfall(trace, items) {
        var wrap = el("div", {className:"alice-trace-waterfall"});
        var head = el("div", {className:"alice-trace-waterfall-head"});
        head.appendChild(el("span",{},"Waterfall"));
        head.appendChild(el("span",{className:"alice-trace-waterfall-sub"},"tools = реальный start/end · Responses/events = timestamps"));
        wrap.appendChild(head);

        var timed = items.filter(function(it){ return Number.isFinite(it.timestamp) || (Number.isFinite(it.start) && Number.isFinite(it.end)); });
        if (!timed.length) { wrap.appendChild(el("div",{className:"alice-trace-notice"},"Trace не содержит временных событий.")); return wrap; }

        var times = [];
        timed.forEach(function(it){
            if(Number.isFinite(it.start)) times.push(it.start);
            if(Number.isFinite(it.end)) times.push(it.end);
            if(Number.isFinite(it.timestamp)) times.push(it.timestamp);
        });
        if (Number.isFinite(Number(trace.created_at))) times.push(Number(trace.created_at));

        var min = Math.min.apply(Math,times);
        var max = Math.max.apply(Math,times);
        var span = Math.max(0.001,max-min);
        var durationMs = span * 1000;
        var timeline = el("div",{className:"alice-trace-timeline"});

        var axis = el("div",{className:"alice-trace-axis-labels"});
        axis.appendChild(el("div",{},""));
        var axisValues = el("div",{className:"alice-trace-axis-values"});
        [0,.25,.5,.75,1].forEach(function(p){ axisValues.appendChild(el("span",{},fmtMs(p*durationMs))); });
        axis.appendChild(axisValues); timeline.appendChild(axis);

        var body = el("div",{className:"alice-trace-timeline-body"});
        [0,.25,.5,.75,1].forEach(function(p){ var line=el("div",{className:"alice-trace-gridline"}); line.style.left="calc(108px + (100% - 108px) * " + p + ")"; body.appendChild(line); });

        function addRow(label, item) {
            var row = el("div",{className:"alice-trace-timeline-row"});
            row.appendChild(el("div",{className:"alice-trace-row-label",title:label},label));
            var track = el("div",{className:"alice-trace-row-track"});
            if (item.kind === "tool" && Number.isFinite(item.start) && Number.isFinite(item.end)) {
                var left = Math.max(0,Math.min(100,((item.start-min)/span)*100));
                var width = Math.max(.9,Math.min(100-left,((item.end-item.start)/span)*100));
                var bar=el("div",{className:"alice-trace-bar tool",title:item.name + " · " + fmtMs((item.end-item.start)*1000)});
                bar.style.left=left+"%"; bar.style.width=width+"%";
                bar.onclick=function(){selectItem(item);};
                track.appendChild(bar);
            } else if (Number.isFinite(item.timestamp)) {
                var point=el("div",{className:"alice-trace-point " + item.kind,title:item.name});
                point.style.left=Math.max(0,Math.min(100,((item.timestamp-min)/span)*100))+"%";
                point.onclick=function(){selectItem(item);};
                track.appendChild(point);
            }
            row.appendChild(track); body.appendChild(row);
        }

        timed.filter(function(it){return it.kind === "tool";}).forEach(function(it){ addRow("🔧 " + it.name,it); });
        timed.filter(function(it){return it.kind === "response";}).forEach(function(it){ addRow("🤖 " + it.name,it); });
        timed.filter(function(it){return it.kind === "event";}).forEach(function(it){ addRow("• " + it.name,it); });

        timeline.appendChild(body); wrap.appendChild(timeline); return wrap;
    }

    function renderMobileTabs(items) {
        var nav = el("div",{className:"alice-trace-mobile-tabs"});
        [
            ["All",function(){renderMobileList(items);}],
            ["Tools",function(){renderMobileList(items.filter(function(i){return i.kind==="tool";}));}],
            ["Responses",function(){renderMobileList(items.filter(function(i){return i.kind==="response";}));}],
            ["Events",function(){renderMobileList(items.filter(function(i){return i.kind==="event";}));}]
        ].forEach(function(pair,i){
            var b=el("button",{className:"alice-trace-mobile-tab"},pair[0]);
            b.onclick=function(){nav.querySelectorAll("button").forEach(function(x){x.classList.remove("active")});b.classList.add("active");pair[1]();};
            if(i===0)b.classList.add("active"); nav.appendChild(b);
        });
        return nav;
    }

    function renderMobileList(items){
        if(!state.nav) return;
        var list=state.nav.querySelector(".alice-trace-mobile-list");
        if(!list){list=el("div",{className:"alice-trace-mobile-list"});state.nav.appendChild(list);}
        list.textContent="";
        items.forEach(function(item){
            var btn=el("button",{className:"alice-trace-item"});
            btn.appendChild(el("span",{className:"alice-trace-icon"},item.kind==="tool"?"🔧":item.kind==="response"?"🤖":"•"));
            var main=el("span",{className:"alice-trace-item-main"}); main.appendChild(el("span",{className:"alice-trace-item-name"},item.name));
            var meta=(item.kind==="tool"&&Number.isFinite(item.start)&&Number.isFinite(item.end))?fmtMs((item.end-item.start)*1000):(Number.isFinite(item.timestamp)?new Date(item.timestamp*1000).toLocaleTimeString("ru-RU"):"");
            main.appendChild(el("span",{className:"alice-trace-item-meta"},meta)); btn.appendChild(main); item._mobileButton=btn;
            btn.onclick=function(){selectItem(item);state.nav.style.display="none";state.body.querySelector(".alice-trace-inspector").style.display="block";}; list.appendChild(btn);
        });
    }

    function renderSidebar(items) {
        var sidebar = el("div",{className:"alice-trace-nav"});
        sidebar.appendChild(el("div",{className:"alice-trace-section-title"},"EVENTS / TRACE"));
        items.forEach(function(item){
            var btn=el("button",{className:"alice-trace-item"});
            btn.appendChild(el("span",{className:"alice-trace-icon"},item.kind==="tool"?"🔧":item.kind==="response"?"🤖":"•"));
            var main=el("span",{className:"alice-trace-item-main"}); main.appendChild(el("span",{className:"alice-trace-item-name"},item.name));
            var meta=(item.kind === "tool" && Number.isFinite(item.start) && Number.isFinite(item.end)) ? fmtMs((item.end-item.start)*1000) : (Number.isFinite(item.timestamp) ? new Date(item.timestamp*1000).toLocaleTimeString("ru-RU") : "");
            main.appendChild(el("span",{className:"alice-trace-item-meta"},meta)); btn.appendChild(main); btn.onclick=function(){selectItem(item);}; item._button=btn; sidebar.appendChild(btn);
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
        area.style.display = "block";
        var inner = el("div",{className:"alice-trace-inspector-inner"});
        if (!item) { inner.appendChild(el("div",{className:"alice-trace-notice"},"Выберите событие или tool call, чтобы открыть JSON inspector.")); area.appendChild(inner); return; }
        inner.appendChild(el("div",{className:"alice-trace-inspector-title"},item.name));
        inner.appendChild(el("div",{className:"alice-trace-inspector-sub"},item.kind.toUpperCase() + (item.data.step ? " · step " + item.data.step : "")));
        var tabs=el("div",{className:"alice-trace-tabs"});
        var selectedTab=tab || tabsFor(item)[0];
        tabsFor(item).forEach(function(t){ var b=el("button",{className:"alice-trace-tab"},t); if(t===selectedTab)b.classList.add("active"); b.onclick=function(){renderInspector(trace,item,t);}; tabs.appendChild(b); });
        inner.appendChild(tabs);
        var content=document.createElement("div");
        function pre(value){ var wrap=el("div",{className:"alice-trace-json-wrap"}); wrap.appendChild(el("pre",{className:"alice-trace-pre"},jsonText(value))); content.appendChild(wrap); }
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
        state.selected=item;
        if(state.modal){ state.modal.querySelectorAll(".alice-trace-item").forEach(function(b){b.classList.remove("active")}); }
        if(item._button)item._button.classList.add("active");
        if(item._mobileButton)item._mobileButton.classList.add("active");
        renderInspector(state.trace,item);
    }

    function copyTrace(){
        var text=JSON.stringify(state.trace,null,2);
        if(navigator.clipboard&&navigator.clipboard.writeText){ navigator.clipboard.writeText(text).catch(function(){ fallbackCopy(text); }); }
        else fallbackCopy(text);
    }
    function fallbackCopy(text){ var ta=document.createElement("textarea"); ta.value=text; document.body.appendChild(ta); ta.select(); try{document.execCommand("copy");}catch(_){} ta.remove(); }

    function close(){ if(state.modal){ state.modal.remove(); state.modal=null; state.trace=null; state.selected=null; state.body=null; state.nav=null; } document.removeEventListener("keydown",onKey); }
    function onKey(e){ if(e.key===ESC) close(); }

    function open(input){
        var trace=normalizeTrace(input); if(!trace) return;
        injectStyles(); close(); state.trace=trace; var items=buildItems(trace);
        var modal=el("div",{className:"alice-trace-modal"}); var win=el("div",{className:"alice-trace-window",role:"dialog","aria-modal":"true","aria-label":"Execution Trace"});
        var header=el("div",{className:"alice-trace-header"}); header.appendChild(el("span",{},"⚡")); header.appendChild(el("div",{className:"alice-trace-title"},"Execution Trace")); header.appendChild(el("div",{className:"alice-trace-sub"},trace.trace_id ? String(trace.trace_id) : "local")); header.appendChild(el("div",{className:"alice-trace-spacer"})); var copy=el("button",{className:"alice-trace-btn",title:"Copy raw JSON"},"Copy JSON"); copy.onclick=copyTrace; header.appendChild(copy); var closeBtn=el("button",{className:"alice-trace-btn",title:"Close"},"×"); closeBtn.onclick=close; header.appendChild(closeBtn); win.appendChild(header);
        win.appendChild(renderMetricRow(trace)); win.appendChild(renderWaterfall(trace,items));
        var mobileNav=renderMobileTabs(items); state.nav=mobileNav; mobileNav.appendChild(el("div",{className:"alice-trace-mobile-list"})); renderMobileList(items); win.appendChild(mobileNav);
        var main=el("div",{className:"alice-trace-main"}); state.body=main; main.appendChild(renderSidebar(items)); main.appendChild(el("div",{className:"alice-trace-inspector"})); win.appendChild(main);
        modal.appendChild(win); document.body.appendChild(modal); state.modal=modal; renderInspector(trace,null); document.addEventListener("keydown",onKey);
    }

    window.openTraceViewer = open;
})();
