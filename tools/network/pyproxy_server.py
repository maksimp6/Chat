#!/usr/bin/env python3
"""HTTP forward proxy with a multi-leg tunnel, for a Linux server.

Serves on one port (80 by default):

- ``CONNECT host:port`` and absolute-form ``http://`` requests, as a plain
  HTTP proxy;
- ``POST /t``: one leg of a tunnel session from ``pyproxy_client.py``. The
  client opens several legs with the same ``X-S`` session key and spreads
  every stream's bytes over all of them, so one download is not limited by
  what a single TCP connection gets on a lossy, long path.

Every request needs ``Proxy-Authorization: Basic`` with ``PROXY_USER`` and
``PROXY_PASS`` from the environment. ``PROXY_HOST`` and ``PROXY_PORT`` set the
listening address.

Usage:
  PROXY_USER=proxy PROXY_PASS=... python3 tools/network/pyproxy_server.py
"""

import asyncio
import base64
import hmac
import logging
import os
import socket
import struct

log = logging.getLogger("logos.tools.pyproxy_server")

# --- multi-leg framing (same as in pyproxy_client.py) -------------------------
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


# --- server ------------------------------------------------------------------


class Proxy:
    def __init__(self, user, password):
        self.expected = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.sessions = {}

    async def tunnel_open(self, stream, target):
        host, _, port = target.rpartition(":")
        try:
            u_reader, u_writer = await asyncio.wait_for(
                asyncio.open_connection(host.strip("[]"), int(port)), 15
            )
        except Exception as e:
            log.info("tunnel open %s failed: %s", target, e)
            stream.session.send(stream.sid, FAIL, 0)
            stream.session.streams.pop(stream.sid, None)
            return
        tune(u_writer)
        stream.session.send(stream.sid, OK, 0)
        await stream.relay(u_reader, u_writer)

    async def tunnel_leg(self, reader, writer, headers):
        key = next((v for k, v in headers if k.lower() == "x-s"), "")
        if not key:
            await reply(writer, "400 Bad Request")
            return
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n\r\n")
        await writer.drain()
        session = self.sessions.get(key)
        if session is None or session.dead:
            session = self.sessions[key] = Session(on_open=self.tunnel_open)
        try:
            await session.add_leg(reader, writer)
        finally:
            if session.dead and self.sessions.get(key) is session:
                del self.sessions[key]

    async def handle(self, c_reader, c_writer):
        tune(c_writer)
        peer = c_writer.get_extra_info("peername")
        try:
            method, target, version, headers = await read_head(c_reader)
        except Exception:
            c_writer.close()
            return

        auth = next((v for k, v in headers if k.lower() == "proxy-authorization"), "")
        if not hmac.compare_digest(auth.encode(), self.expected.encode()):
            c_writer.write(
                b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                b'Proxy-Authenticate: Basic realm="proxy"\r\n'
                b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
            await c_writer.drain()
            c_writer.close()
            log.info("auth fail from %s", peer)
            return

        if method.upper() == "POST" and target == "/t":
            await self.tunnel_leg(c_reader, c_writer, headers)
            return

        if method.upper() == "CONNECT":
            host, _, port = target.rpartition(":")
            try:
                u_reader, u_writer = await asyncio.open_connection(host.strip("[]"), int(port))
            except Exception as e:
                log.info("connect %s failed: %s", target, e)
                await reply(c_writer, "502 Bad Gateway")
                return
            tune(u_writer)
            c_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await c_writer.drain()
        else:
            # Absolute-form request: http://host[:port]/path
            if not target.lower().startswith("http://"):
                await reply(c_writer, "400 Bad Request")
                return
            hostport, _, path = target[7:].partition("/")
            host, _, port = hostport.partition(":")
            try:
                u_reader, u_writer = await asyncio.open_connection(host, int(port or 80))
            except Exception:
                await reply(c_writer, "502 Bad Gateway")
                return
            tune(u_writer)
            drop = {"proxy-authorization", "proxy-connection"}
            out = [f"{method} /{path} {version}"]
            out += [f"{k}: {v}" for k, v in headers if k.lower() not in drop]
            u_writer.write(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
            await u_writer.drain()

        await asyncio.gather(pipe(c_reader, u_writer), pipe(u_reader, c_writer))


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(256 * 1024)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def read_head(reader):
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    method, target, version = lines[0].split(" ", 2)
    headers = []
    for line in lines[1:]:
        if line:
            k, _, v = line.partition(":")
            headers.append((k.strip(), v.strip()))
    return method, target, version, headers


async def reply(writer, status):
    writer.write(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
    await writer.drain()
    writer.close()


async def serve():
    host = os.environ.get("PROXY_HOST", "0.0.0.0")
    port = int(os.environ.get("PROXY_PORT", "80"))
    proxy = Proxy(os.environ["PROXY_USER"], os.environ["PROXY_PASS"])
    server = await asyncio.start_server(proxy.handle, host, port, backlog=1024)
    log.info("listening on %s:%s", host, port)
    async with server:
        await server.serve_forever()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(serve())


if __name__ == "__main__":
    main()
