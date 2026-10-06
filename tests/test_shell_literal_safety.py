import subprocess
import sys

import pytest

from scripts.shell_literal import join_argv


DOLLAR = chr(36)
BACKTICK = chr(96)
PERCENT = chr(37)

CANARIES = [
    PERCENT + "one-million-dollar-cheque",
    DOLLAR + "{NOT_A_VARIABLE}",
    DOLLAR + "(printf SHOULD_NOT_EXECUTE)",
    BACKTICK + "printf SHOULD_NOT_EXECUTE" + BACKTICK,
    "space separated value",
    "single'quote",
    'double"quote',
    "semi;colon",
    "ampersand&&false",
    "pipe|false",
]


@pytest.mark.parametrize("value", CANARIES)
def test_join_argv_preserves_external_values_literally(value):
    command = join_argv(
        [
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1], end='')",
            value,
        ]
    )

    result = subprocess.run(
        ["/bin/bash", "-lc", command],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout == value
    assert result.stderr == ""


def test_join_argv_rejects_empty_command():
    with pytest.raises(ValueError, match="argv must not be empty"):
        join_argv([])