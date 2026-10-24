#!/usr/bin/env python3
"""Подпись APK схемой v1 (JAR) через openssl cms, без apksigner.
resources.arsc, .so и .bin пишутся без сжатия; .so и arsc выровнены на 4 байта.
Использование:
  sign_v1.py in.apk out.apk key.pem cert.pem [имя=путь ...] [--exact N --pad имя]
--exact N: добавить файл-заполнитель --pad так, чтобы весь APK был ровно N байт."""

import base64, hashlib, io, struct, subprocess, sys, tempfile, os, zipfile

CREATED = "Created-By: 1.0 (alice-sign)"
STORED_SUFFIXES = (".arsc", ".so", ".bin")


def b64sha(data):
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def write_entry(out, name, data):
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    if name.endswith(STORED_SUFFIXES):
        info.compress_type = zipfile.ZIP_STORED
        if not name.endswith(".bin"):
            lead = out.fp.tell() + 30 + len(name.encode("utf-8"))
            r = (-lead) % 4
            if r:
                info.extra = struct.pack("<HH", 0xD935, r) + b"\0" * r
    else:
        info.compress_type = zipfile.ZIP_DEFLATED
    out.writestr(info, data)


def read_entries(src, extra, pad_name, pad_size):
    entries = []
    replaced = {name for name, _ in extra} | ({pad_name} if pad_name else set())
    with zipfile.ZipFile(src) as z:
        for info in z.infolist():
            if info.is_dir() or info.filename.startswith("META-INF/") or info.filename in replaced:
                continue
            entries.append((info.filename, z.read(info.filename)))
    for name, path in extra:
        with open(path, "rb") as f:
            entries.append((name, f.read()))
    if pad_name is not None:
        entries.append((pad_name, b"\0" * pad_size))
    return entries


def sign_bytes(entries, key, cert):
    sections = {}
    manifest = "Manifest-Version: 1.0\r\n" + CREATED + "\r\n\r\n"
    for name, data in entries:
        sec = f"Name: {name}\r\nSHA-256-Digest: {b64sha(data)}\r\n\r\n"
        sections[name] = sec
        manifest += sec
    manifest_bytes = manifest.encode("utf-8")

    sf = (
        "Signature-Version: 1.0\r\n" + CREATED + "\r\n"
        f"SHA-256-Digest-Manifest: {b64sha(manifest_bytes)}\r\n\r\n"
    )
    for name, _ in entries:
        sf += f"Name: {name}\r\nSHA-256-Digest: {b64sha(sections[name].encode('utf-8'))}\r\n\r\n"
    sf_bytes = sf.encode("utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        sf_path = os.path.join(tmp, "CERT.SF")
        rsa_path = os.path.join(tmp, "CERT.RSA")
        with open(sf_path, "wb") as f:
            f.write(sf_bytes)
        subprocess.run(
            [
                "openssl",
                "cms",
                "-sign",
                "-binary",
                "-noattr",
                "-md",
                "sha256",
                "-in",
                sf_path,
                "-signer",
                cert,
                "-inkey",
                key,
                "-outform",
                "DER",
                "-out",
                rsa_path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(rsa_path, "rb") as f:
            rsa_bytes = f.read()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        write_entry(out, "META-INF/MANIFEST.MF", manifest_bytes)
        write_entry(out, "META-INF/CERT.SF", sf_bytes)
        write_entry(out, "META-INF/CERT.RSA", rsa_bytes)
        for name, data in entries:
            write_entry(out, name, data)
    return buf.getvalue()


def sign(src, dst, key, cert, extra=(), exact=None, pad_name=None):
    if exact is None:
        data = sign_bytes(read_entries(src, extra, None, 0), key, cert)
    else:
        # Заполнитель берёт почти всё место; остаток (несколько десятков байт, и подпись могла
        # немного измениться по размеру) добирается комментарием в конце zip. Комментарий
        # не входит в подписи v1, поэтому подпись остаётся верной.
        probe = len(sign_bytes(read_entries(src, extra, pad_name, 0), key, cert))
        pad = exact - probe - 256
        if pad < 0:
            sys.exit(f"base content is {probe} bytes, larger than target {exact}")
        data = sign_bytes(read_entries(src, extra, pad_name, pad), key, cert)
        n = exact - len(data)
        if not 0 <= n <= 0xFFFF:
            sys.exit(f"cannot fit: size {len(data)}, target {exact}")
        eocd = data.rfind(b"PK\x05\x06")
        if eocd < 0:
            sys.exit("end of central directory not found")
        data = bytearray(data)
        data[eocd + 20 : eocd + 22] = struct.pack("<H", n)
        data += b"\0" * n
        data = bytes(data)
    with open(dst, "wb") as f:
        f.write(data)


if __name__ == "__main__":
    args = sys.argv[1:]
    exact = pad = None
    if "--exact" in args:
        i = args.index("--exact")
        exact = int(args[i + 1])
        pad = args[args.index("--pad") + 1]
        args = args[:i] + args[i + 2 :]
        if "--pad" in args:
            j = args.index("--pad")
            args = args[:j] + args[j + 2 :]
    if len(args) < 4:
        sys.exit(__doc__)
    extra = []
    for arg in args[4:]:
        name, path = arg.split("=", 1)
        extra.append((name, path))
    sign(args[0], args[1], args[2], args[3], extra, exact, pad)
