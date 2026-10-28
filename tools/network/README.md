# network

`pyproxy`: an HTTP proxy for a Linux server and a Windows client, both on the
Python standard library only.

On a long, lossy path one TCP connection gets a fraction of the link. The
client therefore keeps one session of several TCP legs (16 by default) to the
server on one port and spreads every stream's bytes over all legs; the other
side puts them back in order. One download gets the bandwidth of all legs.

| File | Runs on | What it does |
|---|---|---|
| `pyproxy_server.py` | server | Plain HTTP proxy (`CONNECT`, `http://`) plus tunnel legs (`POST /t`), Basic auth |
| `pyproxy_client.py` | Windows | Local SOCKS5 `127.0.0.1:1081` and HTTP `127.0.0.1:8888`, every connection through the tunnel |
| `pyproxy.service` | server | systemd unit for the server |

## Server

```bash
mkdir -p /opt/pyproxy /etc/pyproxy && chmod 700 /etc/pyproxy
cp tools/network/pyproxy_server.py /opt/pyproxy/
cp tools/network/pyproxy.service /etc/systemd/system/
umask 077; printf 'PROXY_PORT=80\nPROXY_USER=proxy\nPROXY_PASS=%s\n' "$(openssl rand -hex 16)" > /etc/pyproxy/pyproxy.env
systemctl daemon-reload && systemctl enable --now pyproxy
```

The credentials live only in `/etc/pyproxy/pyproxy.env`. BBR on the server
(`net.ipv4.tcp_congestion_control = bbr`) helps every leg.

## Windows client

1. Put `PROXY_URL=http://proxy:<password>@<server>:80` into `%USERPROFILE%\.pyproxy.env`.
2. Run `pythonw.exe tools\network\pyproxy_client.py`; add the same command to
   `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` to start it at logon.
3. For system-wide use, point ProxiFyre at SOCKS5 `127.0.0.1:1081`, TCP only,
   and add `python` to its `excludes` so the client's own legs go direct.

Without a console (`pythonw`) the client logs to `pyproxy_client.log` next to
the script.

## Limits

- TCP only: the local SOCKS5 has no `UDP ASSOCIATE`, so UDP goes direct.
- If one leg breaks, the whole session is reset and its open streams close;
  the next connection opens a new session.
- Too many legs can trip per-host connection limits on the path: on the test
  line 16 legs reached the direct-link speed, 32 were slower.
