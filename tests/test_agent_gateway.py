import json
import time
from urllib.error import HTTPError
import pytest
from agent_gateway import (
    A2AClient,A2AClientConfig,A2AProtocolError,RESTAgentClient,
    AgentAlreadyRegistered,AgentApprovalRequired,AgentCircuitOpenError,
    AgentDescriptor,AgentGateway,AgentInvocationError,AgentNotFound,
    AgentPermissionError,AgentRateLimitError,
)

def test_register_and_invoke():
    g=AgentGateway(default_max_retries=0)
    g.register(AgentDescriptor("echo","Echo",("chat",)), lambda p: {"echo":p["message"]})
    r=g.invoke("echo",{"message":"hi"})
    assert r.status=="completed" and r.result=={"echo":"hi"} and r.attempts==1

def test_duplicate_and_missing():
    g=AgentGateway(default_max_retries=0); d=AgentDescriptor("echo","Echo")
    g.register(d,lambda p:p)
    with pytest.raises(AgentAlreadyRegistered): g.register(d,lambda p:p)
    with pytest.raises(AgentNotFound): g.get("missing")

def test_retry_then_success():
    g=AgentGateway(default_max_retries=1); calls={"n":0}
    def f(_):
        calls["n"]+=1
        if calls["n"]==1: raise RuntimeError("temporary")
        return {"ok":True}
    g.register(AgentDescriptor("retry","Retry"),f)
    r=g.invoke("retry",{})
    assert r.status=="completed" and r.attempts==2 and calls["n"]==2

def test_timeout():
    g=AgentGateway(default_timeout_seconds=.01,default_max_retries=1)
    g.register(AgentDescriptor("slow","Slow"),lambda _:time.sleep(.1))
    r=g.invoke("slow",{})
    assert r.status=="error" and r.attempts==2 and "timed out" in (r.error or "")

def test_circuit_breaker():
    g=AgentGateway(default_max_retries=0,circuit_failure_threshold=2,circuit_reset_seconds=60)
    g.register(AgentDescriptor("broken","Broken"),lambda _:(_ for _ in ()).throw(RuntimeError("boom")))
    assert g.invoke("broken",{}).status=="error"
    assert g.invoke("broken",{}).status=="error"
    r=g.invoke("broken",{})
    assert r.status=="error" and "circuit is open" in (r.error or "")

def test_rate_limit():
    g=AgentGateway(default_max_retries=0,rate_limit_per_agent=1)
    g.register(AgentDescriptor("echo","Echo"),lambda p:p)
    assert g.invoke("echo",{"n":1}).status=="completed"
    r=g.invoke("echo",{"n":2})
    assert r.status=="error" and "rate limit" in (r.error or "")

def test_policy_and_approval():
    g=AgentGateway(default_max_retries=0)
    g.register(AgentDescriptor("secure","Secure",risk_level="high",requires_approval=True,allowed_users=("alice",)),lambda p:p)
    assert g.invoke("secure",{},user_id="mallory",approved=True).status=="error"
    assert g.invoke("secure",{},user_id="alice",approved=False).status=="error"
    assert g.invoke("secure",{},user_id="alice",approved=True).status=="completed"

def test_capability_route_and_fallback():
    g=AgentGateway(default_max_retries=0)
    g.register(AgentDescriptor("a","A",("chat",)),lambda _:(_ for _ in ()).throw(RuntimeError("down")))
    g.register(AgentDescriptor("b","B",("chat",)),lambda p:{"agent":p["value"]})
    r=g.route("chat",{"value":3})
    assert r.status=="completed" and r.agent_id=="b"

def test_trace_events():
    events=[]; errors=[]
    class T:
        def add_event(self,k,p): events.append((k,p))
        def record_error(self,s,m,call_id=None): errors.append((s,m,call_id))
    g=AgentGateway(default_max_retries=0); g.register(AgentDescriptor("e","E"),lambda p:p)
    r=g.invoke("e",{"x":1},trace=T())
    assert r.status=="completed" and events[0][0]=="agent_invocation_started" and events[-1][1]["invocation_id"]==r.invocation_id and not errors

def test_a2a(monkeypatch):
    captured={}
    class R:
        def __enter__(self): return self
        def __exit__(self,*a): return False
        def read(self): return json.dumps({"jsonrpc":"2.0","id":captured["id"],"result":{"ok":True}}).encode()
    def fake(req,timeout):
        captured["id"]=json.loads(req.data.decode())["id"]; captured["auth"]=dict((k.lower(),v) for k,v in req.header_items())
        return R()
    monkeypatch.setattr("agent_gateway.urlopen",fake)
    c=A2AClient(A2AClientConfig("https://agent.example/a2a"),lambda:"token")
    assert c.send_message({"message":{"role":"user","parts":[]}})=={"ok":True}
    assert captured["auth"]["authorization"]=="Bearer token"

def test_a2a_bad_id(monkeypatch):
    class R:
        def __enter__(self): return self
        def __exit__(self,*a): return False
        def read(self): return b'{"jsonrpc":"2.0","id":"wrong","result":{}}'
    monkeypatch.setattr("agent_gateway.urlopen",lambda req,timeout:R())
    with pytest.raises(A2AProtocolError,match="does not match"):
        A2AClient(A2AClientConfig("https://agent.example/a2a")).send_message({"message":{"role":"user","parts":[]}})

def test_rest(monkeypatch):
    class R:
        def __enter__(self): return self
        def __exit__(self,*a): return False
        def read(self): return b'{"ok":true}'
    monkeypatch.setattr("agent_gateway.urlopen",lambda req,timeout:R())
    assert RESTAgentClient("https://agent.example/run").send({"x":1})=={"ok":True}
