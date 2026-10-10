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


def _check_directory(directory: Path, *, shared_gid: int | None = None) -> None:
    info=directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid():
        raise SocketBoundaryError("socket directory must be owned by service")
    if shared_gid is None:
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise SocketBoundaryError("private socket directory must be 0700")
    elif (type(shared_gid) is not int or shared_gid < 0 or
          info.st_gid != shared_gid or stat.S_IMODE(info.st_mode) != 0o710):
        raise SocketBoundaryError("shared socket directory must be owned and 0710")


class SignedInputSocket:
    """One packet per connection, SO_PEERCRED checked before verification."""
    def __init__(self,path:Path,verifier:ProtectedVerifier,*,peer_uid:int,shared_gid:int | None=None):
        if type(peer_uid) is not int or peer_uid<0:
            raise SocketBoundaryError("peer UID required")
        self.path=Path(path)
        self.verifier=verifier
        if shared_gid is not None and (type(shared_gid) is not int or shared_gid < 0):
            raise SocketBoundaryError("invalid shared group")
        self.peer_uid=peer_uid
        self.shared_gid=shared_gid
        self._sock=None
        self._stop=threading.Event()
        self._thread=None
        self._inode=None
        self._parent_fd=None

    def start(self) -> None:
        # Capture directory identity before policy validation and recheck after.
        if self._sock is not None:
            raise SocketBoundaryError("server already started")
        if self.path.name in ("", ".", ".."):
            raise SocketBoundaryError("invalid socket basename")
        parent_fd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        parent_identity=os.fstat(parent_fd)
        try:
            _check_directory(self.path.parent,shared_gid=self.shared_gid)
        except Exception:
            os.close(parent_fd)
            raise
        checked_parent=self.path.parent.lstat()
        if (checked_parent.st_dev,checked_parent.st_ino)!=(parent_identity.st_dev,parent_identity.st_ino):
            os.close(parent_fd)
            raise SocketBoundaryError("socket parent replaced during validation")
        if self.path.exists() or self.path.is_symlink():
            os.close(parent_fd)
            raise SocketBoundaryError("refusing to replace existing socket")
        self._parent_fd=parent_fd
        server=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        try:
            server.settimeout(0.1)
            server.bind(str(self.path))
            current_parent=self.path.parent.lstat()
            if (current_parent.st_dev,current_parent.st_ino)!=(parent_identity.st_dev,parent_identity.st_ino):
                raise SocketBoundaryError("socket parent replaced during bind")
            self._inode=os.stat(self.path.name,dir_fd=parent_fd,follow_symlinks=False).st_ino
            # AF_UNIX socket FD is NOT the filesystem inode on Linux.
            # Use dirfd-relative operations under the pinned, service-owned parent.
            if self.shared_gid is not None:
                os.chown(self.path.name,-1,self.shared_gid,dir_fd=parent_fd,follow_symlinks=False)
            os.chmod(self.path.name,0o660 if self.shared_gid is not None else 0o600,dir_fd=parent_fd,follow_symlinks=False)
            server.listen(4)
        except Exception:
            server.close()
            self._unlink_owned()
            self._close_parent()
            raise
        self._stop.clear()
        self._sock=server
        self._thread=threading.Thread(target=self._run,daemon=True)
        try:
            self._thread.start()
        except Exception:
            self._stop.set()
            server.close()
            self._unlink_owned()
            self._close_parent()
            self._sock=None
            self._thread=None
            raise

    def _unlink_owned(self):
        if self._parent_fd is None or self._inode is None:
            return
        try:
            info=os.stat(self.path.name,dir_fd=self._parent_fd,follow_symlinks=False)
            if stat.S_ISSOCK(info.st_mode) and info.st_ino==self._inode:
                os.unlink(self.path.name,dir_fd=self._parent_fd)
        except FileNotFoundError:
            pass

    def _close_parent(self):
        if self._parent_fd is not None:
            os.close(self._parent_fd)
            self._parent_fd=None
        self._inode=None

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
        self._close_parent()
        self._sock=None
        self._thread=None
