#!/usr/bin/env python3
"""Local SOCKS5 + HTTP proxy that tunnels every connection to pyproxy_server.py.

The client keeps one session of ``PYPROXY_LEGS`` TCP legs (16 by default) to
the server and spreads every stream's bytes over all legs, so one download
gets the bandwidth of all of them. Point a system-wide redirector such as
ProxiFyre at the local SOCKS5 port, and exclude the Python process itself
from it.

Configuration:
  PROXY_URL           http://user:password@server:port, or a ``PROXY_URL=``
                      line in ``~/.pyproxy.env``
  PYPROXY_LEGS        number of TCP legs (16)
  PYPROXY_SOCKS_PORT  local SOCKS5 port on 127.0.0.1 (1081)
  PYPROXY_HTTP_PORT   local HTTP proxy port on 127.0.0.1 (8888)

Usage:
  python tools/network/pyproxy_client.py
"""

import asyncio
import base64
import logging
import os
import socket
import struct
import sys
from urllib.parse import urlsplit

log = logging.getLogger("logos.tools.pyproxy_client")

# --- multi-leg framing (same as in pyproxy_server.py) -------------------------
# Frame: stream_id u32 | type u8 | seq u32 | length u32 | payload
HDR = struct.Struct(">IBII")
OPEN, OK, FAIL, DATA, FIN, RST = range(1, 7)
CHUNK = 32 * 1024
BUF = 1 << 20


def tune(writer):
    sock = writer.get_extra_info("socket")
    if sock is None:
        return
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, BUF)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, BUF)


class Stream:
    def __init__(self, session, sid):
        self.session = session
        self.sid = sid
        self.send_seq = 0
        self.next_recv = 0
        self.pending = {}
        self.queue = asyncio.Queue()
        self.opened = asyncio.get_running_loop().create_future()

    def on_frame(self, ftype, seq, payload):
        if ftype in (OK, FAIL):
            if not self.opened.done():
                self.opened.set_result(ftype == OK)
            return
        if ftype == RST:
            if not self.opened.done():
                self.opened.set_result(False)
            self.queue.put_nowait(None)
            return
        self.pending[seq] = (ftype, payload)
        while self.next_recv in self.pending:
            t, p = self.pending.pop(self.next_recv)
            self.next_recv += 1
            self.queue.put_nowait(p if t == DATA else None)

    def send(self, ftype, payload=b""):
        self.session.send(self.sid, ftype, self.send_seq, payload)
        self.send_seq += 1

    async def pump_in(self, reader):
        """Local socket -> tunnel."""
        try:
            while True:
                data = await reader.read(CHUNK)
                if not data:
                    break
                self.send(DATA, data)
                await self.session.drain()
        except Exception:
            pass
        if not self.session.dead:
            self.send(FIN)

    async def pump_out(self, writer):
        """Tunnel -> local socket."""
        try:
            while True:
                data = await self.queue.get()
                if data is None:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass
            self.session.streams.pop(self.sid, None)

    async def relay(self, reader, writer):
        await asyncio.gather(self.pump_in(reader), self.pump_out(writer))


class Session:
    def __init__(self, on_open=None):
        self.legs = []
        self.streams = {}
        self.dead = False
        self.on_open = on_open

    def add_leg(self, reader, writer):
        tune(writer)
        self.legs.append(writer)
        return asyncio.ensure_future(self._read_leg(reader))

    def _pick(self):
        return min(self.legs, key=lambda w: w.transport.get_write_buffer_size())

    def send(self, sid, ftype, seq, payload=b""):
        if self.dead or not self.legs:
            return
        self._pick().write(HDR.pack(sid, ftype, seq, len(payload)) + payload)

    async def drain(self):
        if self.dead or not self.legs:
            raise ConnectionError("session closed")
        await self._pick().drain()

    async def _read_leg(self, reader):
        try:
            while True:
                sid, ftype, seq, n = HDR.unpack(await reader.readexactly(HDR.size))
                payload = await reader.readexactly(n) if n else b""
                self._dispatch(sid, ftype, seq, payload)
        except Exception:
            pass
        finally:
            self.kill()

    def _dispatch(self, sid, ftype, seq, payload):
        if ftype == OPEN and self.on_open:
            stream = self.streams[sid] = Stream(self, sid)
            asyncio.ensure_future(self.on_open(stream, payload.decode()))
            return
        stream = self.streams.get(sid)
        if stream:
            stream.on_frame(ftype, seq, payload)

    def kill(self):
        if self.dead:
            return
        self.dead = True
        for w in self.legs:
            try:
                w.close()
            except Exception:
                pass
        for stream in list(self.streams.values()):
            stream.on_frame(RST, 0, b"")
        self.streams.clear()


# --- client ------------------------------------------------------------------


def load_server_url():
    url = os.environ.get("PROXY_URL")
    if url:
        return url
    env_file = os.path.join(os.path.expanduser("~"), ".pyproxy.env")
    with open(env_file) as f:
        for line in f:
            if line.startswith("PROXY_URL="):
                return line.split("=", 1)[1].strip()
    raise SystemExit(f"PROXY_URL is not set and {env_file} has no PROXY_URL line")


class Tunnel:
    def __init__(self, url, legs):
        u = urlsplit(url)
        self.host = u.hostname
        self.port = u.port or 80
        self.auth = "Basic " + base64.b64encode(f"{u.username}:{u.password}".encode()).decode()
        self.legs = legs
        self.session = None
        self.lock = asyncio.Lock()
        self.next_sid = 1

    async def _open_leg(self, key):
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), 15
        )
        writer.write(
            (
                f"POST /t HTTP/1.1\r\nHost: {self.host}\r\n"
                f"Proxy-Authorization: {self.auth}\r\nX-S: {key}\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
        if not head.startswith(b"HTTP/1.1 200"):
            writer.close()
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        return reader, writer

    async def get_session(self):
        async with self.lock:
            if self.session and not self.session.dead:
                return self.session
            key = os.urandom(8).hex()
            results = await asyncio.gather(
                *(self._open_leg(key) for _ in range(self.legs)), return_exceptions=True
            )
            legs = [r for r in results if not isinstance(r, BaseException)]
            if not legs:
                raise ConnectionError(f"no legs: {results[0]}")
            session = Session()
            for reader, writer in legs:
                session.add_leg(reader, writer)
            log.info("session %s up with %d/%d legs", key, len(legs), self.legs)
            self.session = session
            self.next_sid = 1
            return session

    async def open(self, target):
        session = await self.get_session()
        sid = self.next_sid
        self.next_sid += 1
        stream = session.streams[sid] = Stream(session, sid)
        session.send(sid, OPEN, 0, target.encode())
        if not await asyncio.wait_for(stream.opened, 20):
            session.streams.pop(sid, None)
            raise ConnectionError(f"open {target} refused")
        return stream


class Local:
    def __init__(self, tunnel):
        self.tunnel = tunnel

    async def handle_socks(self, reader, writer):
        tune(writer)
        try:
            _, n = await reader.readexactly(2)
            methods = await reader.readexactly(n)
            if 0 in methods:
                writer.write(b"\x05\x00")
            else:
                # Accept any username/password: the listener is bound to localhost.
                writer.write(b"\x05\x02")
                await writer.drain()
                _, ulen = await reader.readexactly(2)
                await reader.readexactly(ulen)
                (plen,) = await reader.readexactly(1)
                await reader.readexactly(plen)
                writer.write(b"\x01\x00")
            await writer.drain()
            _, cmd, _, atyp = await reader.readexactly(4)
            if atyp == 1:
                host = ".".join(str(b) for b in await reader.readexactly(4))
            elif atyp == 3:
                (ln,) = await reader.readexactly(1)
                host = (await reader.readexactly(ln)).decode()
            elif atyp == 4:
                raw = await reader.readexactly(16)
                host = "[" + ":".join(raw[i : i + 2].hex() for i in range(0, 16, 2)) + "]"
            else:
                raise ValueError("bad atyp")
            (port,) = struct.unpack(">H", await reader.readexactly(2))
            if cmd != 1:
                writer.write(b"\x05\x07\x00\x01" + b"\x00" * 6)
                await writer.drain()
                writer.close()
                return
            try:
                stream = await self.tunnel.open(f"{host}:{port}")
            except Exception as e:
                log.info("socks %s:%s failed: %s", host, port, e)
                writer.write(b"\x05\x05\x00\x01" + b"\x00" * 6)
                await writer.drain()
                writer.close()
                return
            writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)
            await writer.drain()
            await stream.relay(reader, writer)
        except Exception:
            writer.close()

    async def handle_http(self, reader, writer):
        tune(writer)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except Exception:
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")
        method, target, version = lines[0].split(" ", 2)
        first = b""
        if method.upper() == "CONNECT":
            dest = target
        elif target.lower().startswith("http://"):
            hostport, _, path = target[7:].partition("/")
            dest = hostport if ":" in hostport else hostport + ":80"
            kept = [
                line
                for line in lines[1:]
                if line and not line.lower().startswith(("proxy-authorization:", "proxy-connection:"))
            ]
            first = ("\r\n".join([f"{method} /{path} {version}"] + kept) + "\r\n\r\n").encode("latin-1")
        else:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        try:
            stream = await self.tunnel.open(dest)
        except Exception as e:
            log.info("http %s failed: %s", dest, e)
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        if first:
            stream.send(DATA, first)
        else:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
        await stream.relay(reader, writer)


async def serve():
    legs = int(os.environ.get("PYPROXY_LEGS", "16"))
    socks_port = int(os.environ.get("PYPROXY_SOCKS_PORT", "1081"))
    http_port = int(os.environ.get("PYPROXY_HTTP_PORT", "8888"))
    tunnel = Tunnel(load_server_url(), legs)
    local = Local(tunnel)
    socks = await asyncio.start_server(local.handle_socks, "127.0.0.1", socks_port, backlog=1024)
    http = await asyncio.start_server(local.handle_http, "127.0.0.1", http_port, backlog=1024)
    log.info(
        "server %s:%s, %d legs; SOCKS5 127.0.0.1:%d, HTTP 127.0.0.1:%d",
        tunnel.host, tunnel.port, legs, socks_port, http_port,
    )
    async with socks, http:
        await asyncio.gather(socks.serve_forever(), http.serve_forever())


def main():
    fmt = "%(asctime)s %(message)s"
    if sys.stdout is None:  # pythonw: no console
        log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyproxy_client.log")
        logging.basicConfig(level=logging.INFO, format=fmt, filename=log_file)
    else:
        logging.basicConfig(level=logging.INFO, format=fmt, stream=sys.stdout)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
