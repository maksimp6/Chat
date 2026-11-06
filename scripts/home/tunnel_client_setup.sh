#!/bin/sh
# The owner's server's side of the home's QUIC tunnel (scripts/home/tunnel.py on the Redmi 9).
# Run as root on the server (Linux, x86-64):
#
#   sh tunnel_client_setup.sh key                  # once: makes the key pair and prints the
#                                                  # public half for SERVER_PUBLIC_KEY in tunnel.py
#   sh tunnel_client_setup.sh connect <REDMI_KEY>  # installs sing-box and starts the service;
#                                                  # REDMI_KEY is the base64 line of public.pem
#                                                  # in ~/.config/tunnel on the Redmi
#
# Both sides derive the tunnel's password from their own private key and the other's public
# key, so the password itself never travels.
#
# After connect, 192.168.10.0/24 (the home network) is routed through the tunnel, and
# 127.0.0.1:1081 is a SOCKS5 proxy whose traffic leaves from the home.
set -eu
umask 077

ROOT=/etc/tunnel
HOST=tunnel.maxxxpavlov.ru
VERSION=1.14.4
SHA256=699a1b510aa4396b2176d32d1181920399ebf1e267835da6f3b598d0f7c24968

case ${1:-} in
key)
  mkdir -p "$ROOT"
  [ -f "$ROOT/key.pem" ] || openssl genpkey -algorithm X25519 -out "$ROOT/key.pem"
  openssl pkey -in "$ROOT/key.pem" -pubout
  ;;
connect)
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  curl -fsSL -o "$work/sing-box.tar.gz" \
    "https://github.com/SagerNet/sing-box/releases/download/v$VERSION/sing-box-$VERSION-linux-amd64.tar.gz"
  echo "$SHA256  $work/sing-box.tar.gz" | sha256sum -c -
  tar -xzf "$work/sing-box.tar.gz" -C "$work" --strip-components=1
  install -m 755 "$work/sing-box" /usr/local/bin/sing-box

  printf -- '-----BEGIN PUBLIC KEY-----\n%s\n-----END PUBLIC KEY-----\n' "$2" >"$ROOT/redmi.pem"
  openssl pkeyutl -derive -inkey "$ROOT/key.pem" -peerkey "$ROOT/redmi.pem" -out "$work/shared"
  password=$(sha256sum "$work/shared" | cut -d' ' -f1)

  cat >"$ROOT/config.json" <<EOF
{
  "log": {"level": "warn", "timestamp": true},
  "dns": {"servers": [{"type": "local", "tag": "local"}]},
  "inbounds": [
    {
      "type": "tun",
      "tag": "lan",
      "interface_name": "tunnel0",
      "address": ["172.19.0.1/30"],
      "auto_route": true,
      "route_address": ["192.168.10.0/24"]
    },
    {"type": "socks", "tag": "socks", "listen": "127.0.0.1", "listen_port": 1081}
  ],
  "outbounds": [
    {
      "type": "hysteria2",
      "tag": "home",
      "server": "$HOST",
      "server_port": 443,
      "password": "$password",
      "tls": {"enabled": true, "server_name": "$HOST", "alpn": ["h3"]}
    }
  ],
  "route": {"auto_detect_interface": true, "default_domain_resolver": "local"}
}
EOF
  /usr/local/bin/sing-box check -c "$ROOT/config.json"

  umask 022
  cat >/etc/systemd/system/home-tunnel.service <<EOF
[Unit]
Description=QUIC tunnel to the home network ($HOST)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/sing-box run -c $ROOT/config.json
Restart=always
RestartSec=5
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable home-tunnel
  systemctl restart home-tunnel
  ;;
*)
  echo 'Use: key, or connect <the Redmi public key>' >&2
  exit 2
  ;;
esac
