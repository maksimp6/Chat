import runpy
import sys
import threading

import fakeredis
import pytest
import redis

import db
import tasks.worker as worker
from tasks import (
    MemoryTaskQueue,
    RedisTaskQueue,
    SqlTaskQueue,
    enqueue,
    get_task_queue,
    register_task,
    reset_task_queue,
    run_worker,
)
from tasks.queue import DONE, FAILED, LEASE_EXPIRED_ERROR, QUEUED, RUNNING, TaskQueue


@pytest.fixture(autouse=True)
def _isolated_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "tasks.db"))
    for name in ("ALICE_TASK_QUEUE", "ALICE_REDIS_URL", "ALICE_DB_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALICE_TASK_WORKER", "external")
    reset_task_queue()
    yield
    reset_task_queue()


@pytest.fixture(params=["memory", "sql", "redis"])
def queue(request):
    if request.param == "memory":
        return MemoryTaskQueue()
    if request.param == "sql":
        return SqlTaskQueue()
    return RedisTaskQueue(fakeredis.FakeRedis())


def test_claim_complete_and_stale_lease_holder(queue):
    job_id = queue.enqueue("demo", {"n": 1}, job_id="job-a")
    assert job_id == "job-a"
    assert queue.get("job-a").status == QUEUED

    job = queue.claim("worker-1", lease_seconds=60)
    assert (job.id, job.kind, job.payload, job.status, job.attempts) == (
        "job-a",
        "demo",
        {"n": 1},
        RUNNING,
        1,
    )
    assert queue.claim("worker-2", lease_seconds=60) is None

    assert queue.complete(job, {"ok": True}) is True
    done = queue.get("job-a")
    assert (done.status, done.result, done.error) == (DONE, {"ok": True}, None)
    assert queue.complete(job, {"ok": False}) is False


def test_fail_records_error(queue):
    queue.enqueue("demo", {})
    job = queue.claim("worker-1", lease_seconds=60)
    assert queue.fail(job, "boom") is True
    failed = queue.get(job.id)
    assert (failed.status, failed.error, failed.result) == (FAILED, "boom", None)


def test_expired_lease_is_reclaimed_until_attempts_run_out(queue):
    job_id = queue.enqueue("demo", {}, max_attempts=2)
    first = queue.claim("dead-worker", lease_seconds=-1)
    second = queue.claim("worker-2", lease_seconds=-1)
    assert (second.id, second.attempts) == (job_id, 2)
    assert second.lease_token != first.lease_token
    assert queue.complete(first) is False

    assert queue.claim("worker-3", lease_seconds=60) is None
    failed = queue.get(job_id)
    assert (failed.status, failed.error) == (FAILED, LEASE_EXPIRED_ERROR)


def test_jobs_are_claimed_in_order(queue):
    queue.enqueue("demo", {}, job_id="a")
    queue.enqueue("demo", {}, job_id="b")
    assert queue.claim("w", 60).id == "a"
    assert queue.claim("w", 60).id == "b"


def test_events_are_ordered_and_paged(queue):
    job_id = queue.enqueue("demo", {})
    for index in range(3):
        queue.append_event(job_id, {"type": "step", "index": index, "text": "шаг"})
    assert [seq for seq, _ in queue.events(job_id)] == [1, 2, 3]
    assert queue.events(job_id, after=2) == [(3, {"type": "step", "index": 2, "text": "шаг"})]
    assert queue.events("missing") == []


def test_purge_removes_only_finished_jobs(queue):
    finished = queue.enqueue("demo", {}, job_id="finished")
    queue.append_event(finished, {"type": "x"})
    queue.complete(queue.claim("w", 60))
    queue.enqueue("demo", {}, job_id="pending")

    assert queue.purge(3600) == 0
    assert queue.purge(-1) == 1
    assert queue.get("finished") is None
    assert queue.events("finished") == []
    assert queue.get("pending").status == QUEUED


def test_sql_queue_survives_new_queue_instance():
    SqlTaskQueue().enqueue("demo", {"n": 7}, job_id="durable")
    job = SqlTaskQueue().claim("restarted-worker", 60)
    assert (job.id, job.payload) == ("durable", {"n": 7})


def test_base_queue_is_abstract():
    base = TaskQueue()
    for call in (
        lambda: base.enqueue("demo", {}),
        lambda: base.claim("w", 1),
        lambda: base.complete(None),
        lambda: base.fail(None, "e"),
        lambda: base.get("x"),
        lambda: base.append_event("x", {}),
        lambda: base.events("x"),
        lambda: base.purge(1),
    ):
        with pytest.raises(NotImplementedError):
            call()


def test_get_task_queue_selects_backend(monkeypatch):
    first = get_task_queue()
    assert isinstance(first, SqlTaskQueue)
    assert get_task_queue() is first

    monkeypatch.setenv("ALICE_DB_BACKEND", "memory")
    assert isinstance(get_task_queue(), MemoryTaskQueue)

    monkeypatch.setattr(redis.Redis, "from_url", lambda url: fakeredis.FakeRedis())
    monkeypatch.setenv("ALICE_REDIS_URL", "redis://managed-redis:6379/0")
    assert isinstance(get_task_queue(), RedisTaskQueue)


@pytest.mark.parametrize(
    ("backend", "message"),
    [("redis", "requires ALICE_REDIS_URL"), ("kafka", "unknown ALICE_TASK_QUEUE")],
)
def test_get_task_queue_rejects_bad_config(monkeypatch, backend, message):
    monkeypatch.setenv("ALICE_TASK_QUEUE", backend)
    with pytest.raises(ValueError, match=message):
        get_task_queue()


@register_task("test.echo")
def _echo(context):
    context.emit({"type": "echo", "value": context.payload["value"]})
    return {"echo": context.payload["value"]}


@register_task("test.boom")
def _boom(context):
    raise RuntimeError("handler exploded")


def test_worker_runs_handlers_and_records_failures():
    queue = get_task_queue()
    ok = queue.enqueue("test.echo", {"value": 5})
    bad = queue.enqueue("test.boom", {})
    unknown = queue.enqueue("test.unregistered", {})

    assert run_worker(drain=True, lease_seconds=30) == 3
    assert queue.get(ok).result == {"echo": 5}
    assert queue.events(ok) == [(1, {"type": "echo", "value": 5})]
    assert (queue.get(bad).status, queue.get(bad).error) == (FAILED, "handler exploded")
    assert "no task handler registered" in queue.get(unknown).error


def test_worker_loop_waits_until_stopped(monkeypatch):
    class StopOnWait(threading.Event):
        def wait(self, timeout=None):
            self.waited = timeout
            self.set()
            return True

    stop = StopOnWait()
    monkeypatch.setenv("ALICE_TASK_POLL", "0.25")
    assert run_worker(stop=stop, worker_id="w") == 0
    assert stop.waited == 0.25


def test_inline_mode_runs_enqueued_jobs_in_background(monkeypatch):
    monkeypatch.setenv("ALICE_TASK_WORKER", "inline")
    job_id = enqueue("test.echo", {"value": "inline"})
    worker._INLINE_THREAD.join(timeout=0.2)
    for _ in range(200):
        if get_task_queue().get(job_id).status == DONE:
            break
        threading.Event().wait(0.01)
    assert get_task_queue().get(job_id).result == {"echo": "inline"}


def test_inline_worker_survives_a_failed_pass(monkeypatch):
    monkeypatch.setenv("ALICE_TASK_WORKER", "inline")
    passes = []
    recovered = threading.Event()

    def flaky_run_worker(**kwargs):
        passes.append(kwargs)
        if len(passes) == 1:
            raise RuntimeError("database unavailable")
        recovered.set()
        return 0

    monkeypatch.setattr(worker, "run_worker", flaky_run_worker)
    worker._kick_inline_worker()
    for _ in range(3):
        if recovered.wait(0.2):
            break
        worker._kick_inline_worker()
    assert recovered.is_set()


def test_external_mode_only_enqueues():
    job_id = enqueue("test.echo", {"value": 1})
    assert get_task_queue().get(job_id).status == QUEUED


def test_cli_drains_queue(monkeypatch):
    job_id = get_task_queue().enqueue("test.echo", {"value": "cli"})
    monkeypatch.setattr(sys, "argv", ["tasks", "--drain", "--lease-seconds", "30"])
    sys.modules.pop("tasks.__main__", None)
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("tasks", run_name="__main__")
    assert exit_info.value.code == 0
    assert get_task_queue().get(job_id).status == DONE
    assert "voice_routes" in sys.modules
