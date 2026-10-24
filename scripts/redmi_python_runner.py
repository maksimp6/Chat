#!/usr/bin/env python3
"""Small GitHub Actions runner for trusted Python/shell jobs on Termux.

No containers, .NET or pip dependencies. OpenSSL performs RSA operations.
"""
from __future__ import annotations
import argparse
import base64
import datetime
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

PROTOCOL_VERSION = "2.338.1"
USER_AGENT = "GitHubActionsRunner/2.338.1 RedmiPythonRunner/0.1"
DEFAULT_STATE = Path.home() / "github-python-runner"

def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

class APIError(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f"HTTP {status}: {message}")

class AlreadyRunning(RuntimeError):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise APIError(code, "Redirect refused to preserve credential scope")

class API:
    def __init__(self):
        self.secrets = []

    def mask(self, text):
        secrets = set(self.secrets)
        for secret in tuple(secrets):
            if secret:
                secrets.add(base64.b64encode(secret.encode()).decode())
                secrets.add(base64.b64encode(("x-access-token:" + secret).encode()).decode())
        for secret in sorted(secrets, key=len, reverse=True):
            if secret:
                text = text.replace(secret, "***")
        return re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|eyJ[A-Za-z0-9_.-]+)", "***", text)

    def request(self, url, token, method="GET", data=None, form=None, raw=None, timeout=50):
        host = urllib.parse.urlsplit(url)
        if host.scheme != "https" or not (host.hostname == "api.github.com" or (host.hostname or "").endswith(".actions.githubusercontent.com")):
            raise ValueError("Unexpected GitHub Actions endpoint")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if token:
            headers["Authorization"] = token if token.startswith(("Bearer ", "RemoteAuth ")) else "Bearer " + token
        if form is not None:
            payload = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif raw is not None:
            payload = raw
            headers["Content-Type"] = "text/plain; charset=utf-8"
        elif data is not None:
            payload = json.dumps(data).encode()
            headers["Content-Type"] = "application/json; charset=utf-8"
        else:
            payload = None
        try:
            request = urllib.request.Request(url, data=payload, headers=headers, method=method)
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                body = response.read(16 * 1024 * 1024)
                if not body.strip():
                    return None
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    raise APIError(response.status, "Non-JSON response: " + self.mask(body[:300].decode(errors="replace"))) from None
        except urllib.error.HTTPError as exc:
            body = self.mask(exc.read(3000).decode(errors="replace"))
            raise APIError(exc.code, body[:600]) from None

    def upload_blob(self, url, payload, storage_type):
        """PUT to a signed Results storage URL, without forwarding a job token."""
        target = urllib.parse.urlsplit(url)
        host = target.hostname or ""
        if target.scheme != "https" or target.username or target.password or target.port not in (None, 443):
            raise ValueError("Invalid log storage URL")
        if not (host.endswith(".blob.core.windows.net") or
                re.fullmatch(r"[a-z0-9.-]+\.s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com", host)):
            raise ValueError("Unexpected log storage host: " + host)
        self.secrets.append(url)
        headers = {"Content-Type": "text/plain; charset=utf-8", "User-Agent": USER_AGENT}
        if storage_type == "BLOB_STORAGE_TYPE_AZURE":
            headers["x-ms-blob-type"] = "BlockBlob"
        try:
            request = urllib.request.Request(url, data=payload, headers=headers, method="PUT")
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=40) as response:
                if response.status not in (200, 201):
                    raise APIError(response.status, "Log storage rejected upload")
        except urllib.error.HTTPError as exc:
            raise APIError(exc.code, "Log storage rejected upload") from None
        except urllib.error.URLError:
            raise RuntimeError("Log storage upload unavailable") from None

def secure_json(path, value):
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream)
    temp.replace(path)

def openssl(*args, input=None):
    proc = subprocess.run(["openssl", *args], input=input, capture_output=True)
    if proc.returncode:
        raise RuntimeError("OpenSSL operation failed")
    return proc.stdout

def register(state, repo, name):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (state / "config.json").exists():
        raise ValueError("Already registered; existing identity preserved")
    key = state / "identity.pem"
    if not key.exists():
        old = os.umask(0o077)
        try:
            openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(key))
        finally:
            os.umask(old)
    enc_path = state / "registration.enc"
    token = openssl("pkeyutl", "-decrypt", "-inkey", str(key), "-pkeyopt", "rsa_padding_mode:oaep",
                    "-pkeyopt", "rsa_oaep_md:sha256", input=base64.b64decode(enc_path.read_text())).decode()
    api = API()
    api.secrets.append(token)
    tenant = api.request("https://api.github.com/actions/runner-registration", "RemoteAuth " + token,
                         "POST", {"url": "https://github.com/" + repo, "runner_event": "register"})
    api.secrets.append(tenant["token"])
    base = tenant["url"].rstrip("/")
    print("Registration: locating runner pool", flush=True)
    pools = api.request(base + "/_apis/distributedtask/pools?api-version=5.1-preview.1", tenant["token"])["value"]
    pool = next(item["id"] for item in pools if item.get("isInternal") and not item.get("isHosted"))
    route = base + f"/_apis/distributedtask/pools/{pool}/agents"
    print("Registration: checking duplicate name", flush=True)
    previous = api.request(route + "?agentName=" + urllib.parse.quote(name) + "&api-version=5.1-preview.1", tenant["token"])
    if previous.get("count"):
        raise ValueError("Runner name already registered; refusing to replace it")
    modulus = openssl("rsa", "-in", str(key), "-noout", "-modulus").decode().strip().split("=", 1)[1]
    data = {
        "name": name, "version": PROTOCOL_VERSION, "osDescription": "Android ARM64 / native Python " + platform.python_version(),
        "maxParallelism": 1, "disableUpdate": True, "ephemeral": False,
        "authorization": {"publicKey": {"exponent": "AQAB", "modulus": base64.b64encode(bytes.fromhex(modulus)).decode()}},
        "labels": [{"name": name, "type": 1}, {"name": "termux", "type": 1}],
    }
    print("Registration: submitting Python runner identity", flush=True)
    result = api.request(route + "?api-version=5.1-preview.1", tenant["token"], "POST", data)
    config = {
        "id": result["id"], "name": name, "repo": repo, "pool_id": pool,
        "server_url": base, "authorization": result["authorization"],
        "properties": result.get("properties", {}),
    }
    secure_json(state / "config.json", config)
    enc_path.unlink()
    print("REGISTERED", config["id"], name, flush=True)


def property_value(config, name, default=None):
    value = config.get("properties", {}).get(name, default)
    return value.get("$value", default) if isinstance(value, dict) else value

class Listener:
    def __init__(self, state):
        self.state = state
        self.config = json.loads((state / "config.json").read_text())
        self.api = API()
        self.token = ""
        self.expires = 0
        self.broker = property_value(self.config, "ServerUrlV2")
        if not self.broker or not property_value(self.config, "UseV2Flow"):
            raise ValueError("This small runner requires the current GitHub broker protocol")
        self.session = None
        self.stop = threading.Event()

    def authorize(self):
        if time.time() < self.expires:
            return self.token
        auth = self.config["authorization"]
        client = auth["clientId"]
        url = auth["authorizationUrl"]
        now = int(time.time())
        header = b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
        body = b64(json.dumps({"iss": client, "sub": client, "aud": url,
            "nbf": now - 30, "iat": now, "exp": now + 270, "jti": str(uuid.uuid4())}, separators=(",", ":")).encode())
        message = (header + "." + body).encode()
        signature = openssl("dgst", "-sha256", "-sign", str(self.state / "identity.pem"), input=message)
        assertion = header + "." + body + "." + b64(signature)
        self.api.secrets = [self.token, assertion]
        response = self.api.request(url, None, "POST", form={
            "grant_type": "client_credentials", "client_id": client,
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": assertion,
        })
        self.token = response["access_token"]
        self.expires = time.time() + int(response.get("expires_in", 1800)) - 60
        self.api.secrets.append(self.token)
        return self.token

    def connect(self):
        request = {"sessionId": str(uuid.UUID(int=0)), "ownerName": self.config["name"],
            "agent": {"id": self.config["id"], "name": self.config["name"], "version": PROTOCOL_VERSION,
                      "osDescription": "Android ARM64 native Python"},
            "brokerSession": True}
        self.session = self.api.request(urllib.parse.urljoin(self.broker, "session"), self.authorize(), "POST", request)
        secure_json(self.state / "status.json", {"state": "online", "pid": os.getpid(),
            "runner_id": self.config["id"], "name": self.config["name"], "connected_at": timestamp()})
        print("CONNECTED", self.config["id"], self.config["name"], flush=True)

    def parameters(self, status="Online"):
        return urllib.parse.urlencode({"sessionId": self.session["sessionId"], "status": status,
            "runnerVersion": PROTOCOL_VERSION, "os": "Linux", "architecture": "ARM64", "disableUpdate": "true"})

    def disconnect(self):
        if self.session:
            try:
                self.api.request(urllib.parse.urljoin(self.broker, "session") + "?" +
                    urllib.parse.urlencode({"sessionId": self.session["sessionId"]}), self.authorize(), "DELETE")
            finally:
                self.session = None
                secure_json(self.state / "status.json", {"state": "offline", "stopped_at": timestamp()})

    def probe(self):
        self.connect()
        print("BROKER_SESSION_ACCEPTED", bool(self.session.get("sessionId")), flush=True)
        self.disconnect()


def decode(value):
    if isinstance(value, list):
        return [decode(x) for x in value]
    if not isinstance(value, dict):
        return value
    if "expr" in value or "directive" in value:
        raise ValueError("Expressions are not supported by this small runner")
    if "map" in value:
        return {decode(x.get("key", x.get("Key"))): decode(x.get("value", x.get("Value"))) for x in value["map"]}
    if "seq" in value:
        return [decode(x) for x in value["seq"]]
    if "t" in value:
        if value["t"] in (2, 5):
            return {x["k"]: decode(x["v"]) for x in value.get("d", [])}
        if value["t"] == 1:
            return [decode(x) for x in value.get("a", [])]
        for name in ("s", "b", "n"):
            if name in value:
                return value[name]
    for key in ("lit", "bool", "num"):
        if key in value:
            return value[key]
    if value.get("type") == 2:
        return {}
    return {k: decode(v) for k, v in value.items()}

def github_context(job):
    github = decode(job.get("contextData", {}).get("github", {}))
    # Official ExecutionContext initializes github.token from this secret variable.
    token = job.get("variables", {}).get("system.github.token", {}).get("value")
    if token:
        github["token"] = token
    return github

def job_secrets(job):
    return {str(k).upper(): str(v.get("value", ""))
            for k, v in job.get("variables", {}).items() if v.get("isSecret") is True}

def decode_env(value, github, secrets=None):
    """Resolve explicit token/secret names only; never evaluate arbitrary expressions."""
    if isinstance(value, dict) and "expr" in value:
        expression = value["expr"].strip()
        if expression == "github.token" and github.get("token"):
            return github["token"]
        if re.fullmatch(r"secrets\.[A-Za-z_][A-Za-z0-9_]*", expression):
            return (secrets or {}).get(expression.split(".", 1)[1].upper(), "")
        raise ValueError("Unsupported environment expression")
    if isinstance(value, dict) and "map" in value:
        return {decode(x.get("key", x.get("Key"))): decode_env(x.get("value", x.get("Value")), github, secrets)
                for x in value["map"]}
    if isinstance(value, dict) and "type" not in value and "t" not in value:
        return {k: decode_env(v, github, secrets) for k, v in value.items()}
    return decode(value)

def validate_job(job, repository):
    github = github_context(job)
    if github.get("repository") != repository:
        raise ValueError("Job repository is not authorized")
    event_name = github.get("event_name")
    event = github.get("event", {})
    if event_name not in ("push", "pull_request", "workflow_dispatch"):
        raise ValueError("Only trusted push, pull_request and workflow_dispatch events are supported")
    if event_name == "pull_request":
        head_repo = event.get("pull_request", {}).get("head", {}).get("repo", {})
        if head_repo.get("full_name") != repository or head_repo.get("fork"):
            raise ValueError("Fork pull requests cannot execute on this phone")
    if job.get("jobContainer") or job.get("jobServiceContainers"):
        raise ValueError("Containers are unsupported")
    if job.get("jobOutputs") or job.get("defaults"):
        raise ValueError("Job outputs/defaults are unsupported; use explicit shell/run")
    steps = job.get("steps", [])
    if not steps:
        raise ValueError("An empty job cannot pass")
    for step in steps:
        inputs = decode(step.get("inputs", {}))
        reference = step.get("reference", {})
        if not isinstance(inputs, dict) or "script" not in inputs:
            raise ValueError("Only run steps are supported; uses actions are rejected")
        if reference.get("type") not in (3, "script") or reference.get("name") or reference.get("repositoryType") or reference.get("image"):
            raise ValueError("External and container actions are unsupported")
        if not isinstance(inputs["script"], str) or not inputs["script"].strip():
            raise ValueError("Empty or unresolved script")
        if "${{" in inputs["script"]:
            raise ValueError("Unresolved script expression")
        if inputs.get("shell", "bash") not in ("bash", "sh", "python"):
            raise ValueError("Unsupported shell")
        if step.get("condition", "success()") not in ("success()", "always()", "failure()", "cancelled()"):
            raise ValueError("Unsupported step condition")
        if decode(step.get("continueOnError", False)):
            raise ValueError("continue-on-error is unsupported")
        env = decode_env(step.get("environment", {}), github, job_secrets(job))
        if not isinstance(env, dict) or not all(isinstance(v, (str, int, float, bool)) for v in env.values()):
            raise ValueError("Only literal environment values are supported")
    return github

def execute_script(script, shell, cwd, env, cancel, timeout=300):
    import selectors
    name = cwd / ("step-" + uuid.uuid4().hex + (".py" if shell == "python" else ".sh"))
    name.write_text(script)
    name.chmod(0o600)
    if shell == "python":
        args = [sys.executable, "-u", str(name)]
    elif shell == "bash":
        args = ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", str(name)]
    else:
        args = ["sh", "-e", str(name)]
    process = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    started = time.monotonic()
    chunks = bytearray()
    reason = None
    try:
        while selector.get_map() or process.poll() is None:
            if cancel.is_set() or time.monotonic() - started > timeout or len(chunks) > 1024 * 1024:
                reason = "cancelled" if cancel.is_set() else "timeout/output limit"
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for key, _ in selector.select(0.1):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                elif len(chunks) <= 1024 * 1024:
                    chunks.extend(chunk)
        code = process.wait()
        if reason:
            code = 130 if reason == "cancelled" else 124
        return code, chunks.decode(errors="replace") + (("\n" + reason) if reason else "")
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        name.unlink(missing_ok=True)

class Job:
    def __init__(self, listener, message, reference):
        self.listener, self.message, self.reference = listener, message, reference
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.api = API()
        self.error = None
        self.id = message["jobId"]
        endpoint = next(e for e in message["resources"]["endpoints"] if e["name"].lower() == "systemvssconnection")
        parameters = endpoint["authorization"]["parameters"]
        self.token = next(v for k, v in parameters.items() if k.lower() == "accesstoken")
        self.url = endpoint["url"]
        self.api.secrets.append(self.token)
        for item in message.get("variables", {}).values():
            if item.get("isSecret"):
                self.api.secrets.append(str(item.get("value", "")))
        github = github_context(message)
        if github.get("token"):
            self.api.secrets.append(github["token"])
        self.results_url = message.get("variables", {}).get("system.github.results_endpoint", {}).get("value")

    def upload_log(self, text, step_id=None):
        if not self.results_url:
            return False
        kind = "Step" if step_id else "Job"
        data = {"workflow_run_backend_id": self.message["plan"]["planId"],
                "workflow_job_run_backend_id": self.id}
        if step_id:
            data["step_backend_id"] = step_id
        route = urllib.parse.urljoin(self.results_url, "twirp/results.services.receiver.Receiver/")
        signed = self.api.request(route + "Get" + kind + "LogsSignedBlobURL", self.token, "POST", data)
        lines = self.api.mask(text).splitlines() or ["(no output)"]
        payload = "".join(timestamp() + " " + line + "\n" for line in lines).encode()
        limit = signed.get("soft_size_limit")
        if limit and len(payload) > int(limit):
            raise ValueError("Log exceeds Results storage size limit")
        self.api.upload_blob(signed["logs_url"], payload, signed.get("blob_storage_type"))
        data.update(uploaded_at=timestamp(), line_count=len(lines))
        result = self.api.request(route + "Create" + kind + "LogsMetadata", self.token, "POST", data)
        if not result or result.get("ok") is not True:
            raise RuntimeError("Actions log finalization failed")
        return True

    def request(self, path, data):
        return self.api.request(urllib.parse.urljoin(self.url, path), self.token, "POST", data)

    def renew(self):
        while not self.finished.wait(20):
            try:
                self.request("renewjob", {"planId": self.message["plan"]["planId"], "jobId": self.id})
            except Exception as exc:
                self.error = self.api.mask(str(exc))
                self.cancel.set()
                return

    def run(self):
        result = 2
        step_results, output = [], []
        renew = threading.Thread(target=self.renew, daemon=True)
        renew.start()
        try:
            github = validate_job(self.message, self.listener.config["repo"])
            work = self.listener.state / "work" / self.id
            work.mkdir(mode=0o700, parents=True, exist_ok=True)
            env = {k: v for k, v in os.environ.items() if k in
                   ("HOME", "PATH", "PREFIX", "TMPDIR", "LD_PRELOAD", "LANG", "ANDROID_ROOT", "ANDROID_DATA")}
            env.update({"CI": "true", "GITHUB_ACTIONS": "true", "RUNNER_NAME": self.listener.config["name"],
                        "RUNNER_OS": "Linux", "RUNNER_ARCH": "ARM64", "GITHUB_WORKSPACE": str(work)})
            for key in ("repository", "sha", "ref", "run_id", "run_number", "run_attempt", "actor", "event_name", "workflow", "job"):
                if isinstance(github.get(key), (str, int)):
                    env["GITHUB_" + key.upper()] = str(github[key])
            for block in self.message.get("environmentVariables", []):
                env.update({k: str(v) for k, v in decode_env(block, github, job_secrets(self.message)).items()})
            result = 0
            for number, step in enumerate(self.message["steps"], 1):
                condition = step.get("condition", "success()")
                should_run = condition == "always()" or (condition == "success()" and result == 0) or (condition == "failure()" and result == 2) or (condition == "cancelled()" and self.cancel.is_set())
                started = timestamp()
                record = {"external_id": step["id"], "number": number, "name": decode(step.get("displayNameToken")) or step.get("name") or "run",
                          "status": "completed", "conclusion": "skipped", "started_at": started, "completed_at": started}
                if should_run and not self.cancel.is_set():
                    inputs = decode(step["inputs"])
                    target = (work / inputs.get("workingDirectory", ".")).resolve()
                    if not target.is_relative_to(work.resolve()):
                        raise ValueError("Working directory leaves job workspace")
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                    step_env = dict(env)
                    step_env.update({k: str(v) for k, v in decode_env(step.get("environment", {}), github, job_secrets(self.message)).items()})
                    limit = min(900, max(1, float(decode(step.get("timeoutInMinutes")) or 5) * 60))
                    code, text_output = execute_script(inputs["script"], inputs.get("shell", "bash"), target, step_env, self.cancel, limit)
                    text_output = self.api.mask(text_output)
                    conclusion = 3 if self.cancel.is_set() else (0 if code == 0 else 2)
                    record.update({"conclusion": {0: "succeeded", 2: "failed", 3: "canceled"}[conclusion], "completed_at": timestamp(),
                        "annotations": [{"level": 1 if conclusion == 0 else 3, "message": text_output[-6000:] or f"Exit code: {code}", "title": "Native Termux shell output"}]})
                    output.append({"step": number, "exit_code": code, "output": text_output})
                    if conclusion != 0:
                        result = conclusion
                    secure_json(work / ("step-" + str(number) + "-receipt.json"), output[-1])
                    self.upload_log(text_output, step["id"])
                step_results.append(record)
            if self.error:
                raise RuntimeError(self.error)
            if self.cancel.is_set():
                result = 3
            self.upload_log("\n".join("Step " + str(item["step"]) + " (exit " +
                                      str(item["exit_code"]) + ")\n" + item["output"] for item in output))
        except Exception as exc:
            result = 3 if self.cancel.is_set() else 2
            output.append({"error": self.api.mask(str(exc))})
        finally:
            self.finished.set()
        try:
            self.request("completejob", {"planId": self.message["plan"]["planId"], "jobId": self.id,
                "conclusion": {0: "succeeded", 2: "failed", 3: "canceled"}[result], "outputs": {}, "stepResults": step_results,
                "annotations": [{"level": 1 if result == 0 else 3,
                    "message": self.api.mask(json.dumps(output, ensure_ascii=False))[-6000:],
                    "title": "redmi9-python execution receipt"}],
                "billingOwnerId": self.reference.get("billing_owner_id")})
            receipt = {"job_id": self.id, "request_id": self.message.get("requestId"), "conclusion": result,
                       "reported_at": timestamp(), "steps": output}
            secure_json(self.listener.state / "last-result.json", receipt)
            print("JOB_REPORTED", self.id, result, flush=True)
        except Exception as exc:
            self.error = self.api.mask(str(exc))
            secure_json(self.listener.state / "unreported-result.json",
                        {"job_id": self.id, "conclusion": result, "error": self.error, "steps": output})
            print("JOB_REPORT_ERROR", self.error, flush=True)
            self.listener.stop.set()

def run_listener(listener):
    active = None
    thread = None
    def shutdown(*_):
        listener.stop.set()
        if active and thread and thread.is_alive():
            active.cancel.set()
        else:
            raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    def poll_deadline(*_):
        raise TimeoutError("Broker poll deadline")
    signal.signal(signal.SIGALRM, poll_deadline)
    lock = (listener.state / "runner.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise AlreadyRunning("Runner is already running") from None
    lock.seek(0); lock.truncate(); lock.write(str(os.getpid())); lock.flush()
    errors = 0
    listener.connect()
    try:
        while not listener.stop.is_set():
            if thread and not thread.is_alive():
                active, thread = None, None
            try:
                query = listener.parameters("Busy" if active else "Online")
                # Busy long polls must end so a completed job can advertise Online.
                # A socket timeout alone can be extended indefinitely by heartbeat bytes.
                token = listener.authorize()
                signal.setitimer(signal.ITIMER_REAL, 5 if active else 70)
                try:
                    message = listener.api.request(urllib.parse.urljoin(listener.broker, "message") + "?" + query,
                                                   token, timeout=65)
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                errors = 0
                if not message:
                    continue
                kind = message.get("messageType", "")
                body = message.get("body", {})
                print("MESSAGE", kind, flush=True)
                body = json.loads(body) if isinstance(body, str) and body.strip() else (body or {})
                if kind in ("JobCancellation", "PipelineAgentJobRequestCancel"):
                    if active and body.get("jobId") == active.id:
                        active.cancel.set()
                    continue
                if kind not in ("RunnerJobRequest", "JobRequest"):
                    raise RuntimeError("Unsupported broker message " + kind)
                # A job may finish while the broker's long poll is pending.
                if thread and not thread.is_alive():
                    active, thread = None, None
                if active:
                    raise RuntimeError("Received concurrent job")
                if body.get("should_acknowledge"):
                    listener.api.request(urllib.parse.urljoin(listener.broker, "acknowledge") + "?" + query,
                        listener.authorize(), "POST", {"runnerRequestId": body["runner_request_id"]})
                service = body.get("run_service_url")
                if not service:
                    raise RuntimeError("Legacy job transport is not supported")
                job = listener.api.request(urllib.parse.urljoin(service, "acquirejob"), listener.authorize(), "POST",
                    {"jobMessageId": body["runner_request_id"], "runnerOS": "Linux", "billingOwnerId": body.get("billing_owner_id")})
                safe_structure = {"keys": list(job), "message_type": job.get("messageType"),
                    "step_keys": [list(s) for s in job.get("steps", [])],
                    "input_map_keys": [[list(x) for x in s.get("inputs", {}).get("map", [])] for s in job.get("steps", [])]}
                secure_json(listener.state / "job-structure.json", safe_structure)
                active = Job(listener, job, body)
                print("JOB_ACQUIRED", active.id, flush=True)
                thread = threading.Thread(target=active.run, daemon=False)
                thread.start()
            except (TimeoutError, urllib.error.URLError) as exc:
                if isinstance(exc, TimeoutError) and active:
                    continue
                errors += 1
                print("CONNECTION_RETRY", type(exc).__name__, flush=True)
                listener.stop.wait(min(60, 2 ** min(errors, 6)))
            except APIError as exc:
                print("API_ERROR", listener.api.mask(str(exc)), flush=True)
                if exc.status == 401:
                    listener.expires = 0
                elif exc.status < 500:
                    raise
                errors += 1
                listener.stop.wait(min(60, 2 ** min(errors, 6)))
    finally:
        if active:
            active.cancel.set()
        if thread:
            thread.join(timeout=20)
        try:
            listener.disconnect()
        finally:
            lock.close()

def running_pid(state):
    with (state / "runner.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return None
        except BlockingIOError:
            lock.seek(0)
            pid = int(lock.read())
            command = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
            if str(Path(__file__).resolve()).encode() not in command:
                raise RuntimeError("Lock owner does not match this runner; refusing to signal it")
            return pid

def control(state, command):
    if not (state / "config.json").exists():
        raise ValueError("Runner is not registered")
    with (state / "control.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        pid = running_pid(state)
        if command == "stop":
            if pid:
                os.kill(pid, signal.SIGTERM)
            print("STOP_REQUESTED" if pid else "STOPPED", pid or "", flush=True)
        elif command == "start" and not pid:
            log = state / "runner.log"
            if log.exists() and log.stat().st_size > 1024 * 1024:
                log.replace(state / "runner.log.1")
            with log.open("ab", buffering=0) as stream:
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve", "--state", str(state.resolve())],
                    stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
            print("STARTED", process.pid, flush=True)
        else:
            status = json.loads((state / "status.json").read_text()) if pid and (state / "status.json").exists() else {}
            print(json.dumps({"running": bool(pid), "pid": pid, "connection": status}), flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["register", "probe", "run", "serve", "start", "stop", "status"])
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--repo", default="maksimp6/Chat")
    parser.add_argument("--name", default="redmi9-python")
    args = parser.parse_args()
    try:
        if args.command in ("start", "stop", "status"):
            control(args.state, args.command)
        elif args.command == "register":
            register(args.state, args.repo, args.name)
        elif args.command == "probe":
            Listener(args.state).probe()
        elif args.command == "run":
            run_listener(Listener(args.state))
        else:
            while True:
                listener = Listener(args.state)
                try:
                    run_listener(listener)
                except AlreadyRunning:
                    raise
                except Exception as exc:
                    print("RESTART", listener.api.mask(str(exc)), flush=True)
                if listener.stop.is_set():
                    return 0
                time.sleep(10)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
