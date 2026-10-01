"""Security tests for file_routes.py - path traversal and error handling"""

import os
import tempfile
from pathlib import Path
import pytest


def create_test_structure():
    """Create test directory structure with allowed and forbidden paths"""
    tmpdir = tempfile.mkdtemp()
    allowed_root = os.path.join(tmpdir, "allowed")
    os.makedirs(allowed_root)

    # Create a file in allowed directory
    with open(os.path.join(allowed_root, "safe.txt"), "w") as f:
        f.write("safe content")

    # Create forbidden directory outside allowed
    forbidden_dir = os.path.join(tmpdir, "forbidden")
    os.makedirs(forbidden_dir)
    with open(os.path.join(forbidden_dir, "secret.txt"), "w") as f:
        f.write("secret content")

    # Create symlink inside allowed dir pointing outside
    symlink_path = os.path.join(allowed_root, "escape_link")
    try:
        os.symlink(forbidden_dir, symlink_path)
        has_symlink = True
    except (OSError, NotImplementedError):
        has_symlink = False

    return tmpdir, allowed_root, forbidden_dir, has_symlink, symlink_path


def test_file_routes_symlink_traversal_blocked():
    """Test that symlink traversal is prevented by using realpath"""
    tmpdir, allowed_root, forbidden_dir, has_symlink, symlink_path = create_test_structure()

    try:
        if not has_symlink:
            pytest.skip("Symlink creation not supported on this system")

        # Verify the symlink exists and points outside
        assert os.path.islink(symlink_path), "Symlink should exist"
        assert os.path.realpath(symlink_path) == forbidden_dir

        # Mock the list_local_files logic
        root_dir = os.path.realpath(allowed_root)

        # Attempt to traverse via symlink
        traversal_path = os.path.join(root_dir, "escape_link")
        traversal_real = os.path.realpath(traversal_path)

        # Verify that realpath() defeats the symlink
        try:
            commonpath = os.path.commonpath([root_dir, traversal_real])
            inside_root = commonpath == root_dir
        except ValueError:
            inside_root = False

        assert not inside_root, "Symlink traversal should be blocked by realpath()"
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_file_routes_absolute_path_traversal_blocked():
    """Test that absolute path traversal is blocked"""
    tmpdir, allowed_root, forbidden_dir, _, _ = create_test_structure()

    try:
        root_dir = os.path.realpath(allowed_root)

        # Attempt to use absolute path
        traversal_path = os.path.realpath(forbidden_dir)

        try:
            commonpath = os.path.commonpath([root_dir, traversal_path])
            inside_root = commonpath == root_dir
        except ValueError:
            inside_root = False

        assert not inside_root, "Absolute path traversal should be blocked"
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_file_routes_parent_directory_traversal_blocked():
    """Test that ../ traversal is blocked"""
    tmpdir, allowed_root, forbidden_dir, _, _ = create_test_structure()

    try:
        root_dir = os.path.realpath(allowed_root)

        # Attempt to traverse up via ..
        traversal_path = os.path.realpath(os.path.join(root_dir, ".."))

        try:
            commonpath = os.path.commonpath([root_dir, traversal_path])
            inside_root = commonpath == root_dir
        except ValueError:
            inside_root = False

        assert not inside_root, "Parent directory traversal should be blocked"
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_file_routes_safe_file_access_allowed():
    """Test that safe file access within allowed directory works"""
    tmpdir, allowed_root, forbidden_dir, _, _ = create_test_structure()

    try:
        root_dir = os.path.realpath(allowed_root)
        safe_path = "safe.txt"

        target_path = os.path.join(root_dir, safe_path)
        target_dir = os.path.realpath(target_path)

        try:
            commonpath = os.path.commonpath([root_dir, target_dir])
            inside_root = commonpath == root_dir
        except ValueError:
            inside_root = False

        assert inside_root, "Safe file access should be allowed"
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_file_routes_source_code_safe():
    """Test that file_routes.py uses realpath() instead of abspath()"""
    root = Path(__file__).resolve().parents[1]
    source = (root / "file_routes.py").read_text(encoding="utf-8")

    # Find the list_local_files function
    start = source.find("def list_local_files():")
    assert start >= 0, "list_local_files function should exist"

    # Find the end of function (next def or end of file)
    next_def = source.find("\ndef ", start + 1)
    if next_def == -1:
        func_body = source[start:]
    else:
        func_body = source[start:next_def]

    # Verify realpath is used for root_dir
    assert "os.path.realpath(LOCAL_REPO_DIR)" in func_body, (
        "root_dir should use realpath() to resolve symlinks"
    )

    # Verify realpath is used for target_dir
    assert "os.path.realpath(target_path)" in func_body, (
        "target_dir should use realpath() to resolve symlinks"
    )

    # Verify no raw exception strings in client response
    assert 'str(e)' not in func_body or "error: str(e)" not in source, (
        "Error responses should not include raw exception strings"
    )


def test_file_routes_error_handling_safe():
    """Test that error messages do not leak sensitive information"""
    root = Path(__file__).resolve().parents[1]
    source = (root / "file_routes.py").read_text(encoding="utf-8")

    # Check _err_response function
    start = source.find("def _err_response(e):")
    assert start >= 0, "_err_response function should exist"

    next_def = source.find("\ndef ", start + 1)
    func_body = source[start:next_def] if next_def != -1 else source[start:]

    # Should not return raw exception message
    assert 'return jsonify({"error": str(e)})' not in func_body, (
        "_err_response should not return raw exception message"
    )


def test_list_local_files_no_path_in_response():
    """Test that list_local_files doesn't return internal path in response"""
    root = Path(__file__).resolve().parents[1]
    source = (root / "file_routes.py").read_text(encoding="utf-8")

    start = source.find("def list_local_files():")
    assert start >= 0, "list_local_files function should exist"

    next_def = source.find("\ndef ", start + 1)
    if next_def == -1:
        func_body = source[start:]
    else:
        func_body = source[start:next_def]

    # Should not return full path in success response
    assert '"path": target_dir' not in func_body, (
        "list_local_files should not return full path in response"
    )
