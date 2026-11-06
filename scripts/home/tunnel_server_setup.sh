#!/bin/sh
# The far end of the home's QUIC links (scripts/home/tunnel.py on the Redmi 9): the owner's
# server. Run as root (Linux, x86-64):
#
#   sh tunnel_server_setup.sh key                  # once: makes the key pair and the certificate
#                                                  # and prints their public halves, which go into
#                                                  # SERVER_PUBLIC_KEY and SERVER_CERTIFICATE in
#                                                  # tunnel.py
#   sh tunnel_server_setup.sh connect <REDMI_KEY>  # installs sing-box and starts the service;
#                                                  # REDMI_KEY is the base64 line of public.pem
#                                                  # in ~/.config/tunnel on the Redmi
#
# Both sides derive the password from their own private key and the other's public key, so only
# public keys are exchanged to set it up. After that it is sent only inside TLS, at login.
#
# After connect, 192.168.10.0/24 (the home network) is routed through tunnel.maxxxpavlov.ru,
# and UDP 443 here takes what the Redmi's SOCKS5 proxy carries out to the internet.
set -eu
umask 077

ROOT=/etc/tunnel
HOST=tunnel.maxxxpavlov.ru
NAME=maxxxpavlov.online
VERSION=1.14.4
SHA256=699a1b510aa4396b2176d32d1181920399ebf1e267835da6f3b598d0f7c24968

case ${1:-} in
key)
  mkdir -p "$ROOT"
  [ -f "$ROOT/key.pem" ] || openssl genpkey -algorithm X25519 -out "$ROOT/key.pem"
  [ -f "$ROOT/cert.key" ] || openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -nodes -keyout "$ROOT/cert.key" -out "$ROOT/cert.pem" -subj "/CN=$NAME" \
    -addext "subjectAltName=DNS:$NAME" -days 36500
  openssl pkey -in "$ROOT/key.pem" -pubout
  cat "$ROOT/cert.pem"
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

  # dns_mode: its default for a route like this one would take over the server's own DNS.
  # The quic rules: the proxy's way out is the internet, not this machine or its networks.
  # Names are resolved first, so the check sees the addresses the connection then uses.
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
      "route_address": ["192.168.10.0/24"],
      "dns_mode": "disabled"
    },
    {
      "type": "hysteria2",
      "tag": "quic",
      "listen": "0.0.0.0",
      "listen_port": 443,
      "users": [{"name": "redmi", "password": "$password"}],
      "tls": {
        "enabled": true,
        "alpn": ["h3"],
        "certificate_path": "$ROOT/cert.pem",
        "key_path": "$ROOT/cert.key"
      }
    }
  ],
  "outbounds": [
    {
      "type": "hysteria2",
      "tag": "home",
      "server": "$HOST",
      "server_port": 443,
      "password": "$password",
      "tls": {"enabled": true, "server_name": "$HOST", "alpn": ["h3"]}
    },
    {"type": "direct", "tag": "direct"}
  ],
  "route": {
    "auto_detect_interface": true,
    "default_domain_resolver": "local",
    "rules": [
      {"inbound": ["quic"], "action": "resolve"},
      {"inbound": ["quic"], "ip_is_private": true, "action": "reject"},
      {"inbound": ["quic"], "action": "route", "outbound": "direct"}
    ]
  }
}
EOF
  /usr/local/bin/sing-box check -c "$ROOT/config.json"

  umask 022
  cat >/etc/systemd/system/home-tunnel.service <<EOF
[Unit]
Description=QUIC links to the home network ($HOST)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/sing-box run --disable-color -c $ROOT/config.json
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
