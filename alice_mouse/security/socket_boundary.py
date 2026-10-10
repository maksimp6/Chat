"""Isolated, peer-checked signed-input socket for Alice Input.

Not a root daemon or a production launcher. An authorized privileged owner
must provide the private directory, key and provisioned verifier. No plaintext
fallback, no remote sign/provision endpoint and no device operations here.
"""
from __future__ import annotations

import os
import socket
import stat
import struct
import threading
from pathlib import Path
from .grants import MAX_PACKET, GrantError, ProtectedVerifier


class SocketBoundaryError(RuntimeError):
    pass


def _check_directory(directory: Path) -> None:
    info=directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
        raise SocketBoundaryError("socket directory must be private and owned by service")


class SignedInputSocket:
    """One packet per connection, SO_PEERCRED checked before verification."""
    def __init__(self,path:Path,verifier:ProtectedVerifier,*,peer_uid:int):
        if type(peer_uid) is not int or peer_uid<0:
            raise SocketBoundaryError("peer UID required")
        self.path=Path(path)
        self.verifier=verifier
        self.peer_uid=peer_uid
        self._sock=None
        self._stop=threading.Event()
        self._thread=None
        self._inode=None

    def start(self) -> None:
        _check_directory(self.path.parent)
        if self.path.exists() or self.path.is_symlink():
            raise SocketBoundaryError("refusing to replace existing socket")
        server=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        try:
            server.settimeout(0.1)
            server.bind(str(self.path))
            self._inode=self.path.lstat().st_ino
            os.chmod(self.path,0o600)
            server.listen(4)
        except Exception:
            server.close()
            self._unlink_owned()
            raise
        self._sock=server
        self._thread=threading.Thread(target=self._run,daemon=True)
        self._thread.start()

    def _unlink_owned(self):
        try:
            info=self.path.lstat()
            if stat.S_ISSOCK(info.st_mode) and info.st_ino==self._inode:
                self.path.unlink()
        except FileNotFoundError:
            pass

    def _run(self):
        while not self._stop.is_set():
            try:
                conn,_=self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(0.5)
                try:
                    if not hasattr(socket,"SO_PEERCRED"):
                        raise SocketBoundaryError("peer credentials unavailable")
                    raw=conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize("3i"))
                    _,uid,_=struct.unpack("3i",raw)
                    if uid!=self.peer_uid:
                        raise SocketBoundaryError("peer UID mismatch")
                    packet=conn.recv(MAX_PACKET+1)
                    if not packet or len(packet)>MAX_PACKET:
                        raise SocketBoundaryError("oversized or empty grant")
                    accepted=self.verifier.accept(packet)
                    conn.sendall(b"OK\n" if accepted else b"DENIED\n")
                except (GrantError,SocketBoundaryError,OSError,TimeoutError,ValueError):
                    try:conn.sendall(b"DENIED\n")
                    except OSError:pass

    def stop(self):
        self._stop.set()
        if self._sock is not None:
            self._sock.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                raise SocketBoundaryError("server did not stop")
        self._unlink_owned()
        self._sock=None
        self._thread=None
