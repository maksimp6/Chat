"""Private generated image artifacts, kept outside Kristall's bounded text values."""

import os
import re
import stat
import uuid

_SAFE_CONVERSATION = re.compile(r"[A-Za-z0-9_-]{1,128}")
_SAFE_REQUEST = re.compile(r"[A-Za-z0-9_-]{16,128}")


def _directory(conv_id, create=False):
    from storage.records import memory_file_path

    if not isinstance(conv_id, str) or not _SAFE_CONVERSATION.fullmatch(conv_id):
        raise ValueError("invalid image conversation")
    parent_fd = os.open(
        memory_file_path().parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        if create:
            try:
                os.mkdir("generated-images", mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
            os.fsync(parent_fd)
        root_fd = os.open(
            "generated-images",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    finally:
        os.close(parent_fd)
    try:
        os.fchmod(root_fd, 0o700)
        if create:
            try:
                os.mkdir(conv_id, mode=0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
            os.fsync(root_fd)
        conv_fd = os.open(
            conv_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
        )
        try:
            os.fchmod(conv_fd, 0o700)
        except Exception:
            os.close(conv_fd)
            raise
        return conv_fd
    finally:
        os.close(root_fd)


def _filename(request_id):
    if not isinstance(request_id, str) or not _SAFE_REQUEST.fullmatch(request_id):
        raise ValueError("invalid image request")
    return request_id + ".img"


def save_image_file(conv_id, request_id, image):
    filename = _filename(request_id)
    directory_fd = _directory(conv_id, create=True)
    temporary = "." + request_id + "." + uuid.uuid4().hex + ".tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(image)
            output.flush()
            os.fsync(output.fileno())
        os.replace(
            temporary, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd
        )
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def open_image_file(conv_id, request_id, expected_bytes=None):
    filename = _filename(request_id)
    directory_fd = _directory(conv_id)
    try:
        descriptor = os.open(
            filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
        )
    finally:
        os.close(directory_fd)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
            raise ValueError("invalid image artifact")
        if expected_bytes is not None and (
            not isinstance(expected_bytes, int) or info.st_size != expected_bytes
        ):
            raise ValueError("invalid image artifact")
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


def image_file_metadata(conv_id, request_id):
    """Recover a fully renamed artifact if the worker stopped before its receipt commit."""
    with open_image_file(conv_id, request_id) as image:
        header = image.read(8)
        size = os.fstat(image.fileno()).st_size
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", size
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", size
    raise ValueError("invalid generated image format")


def delete_image_files(conv_id):
    """Delete this conversation's artifacts without traversing links or other directories."""
    try:
        directory_fd = _directory(conv_id)
    except FileNotFoundError:
        return
    try:
        for filename in os.listdir(directory_fd):
            os.unlink(filename, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
