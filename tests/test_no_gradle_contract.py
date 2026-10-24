"""RED contract for #1423: no Gradle files or executable integration.

Run independently of application dependencies:
    python -m unittest discover -s tests -p test_no_gradle_contract.py -v

Documentation may explain the ban; it is not an executable dependency.
"""

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRADLE = re.compile(r"\bgradle(?:w)?\b", re.IGNORECASE)
SOURCE_SUFFIXES = {".sh", ".py", ".yml", ".yaml", ".json", ".toml", ".kt", ".java"}


def tracked_paths():
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [Path(name.decode()) for name in output.split(b"\0") if name]


class NoGradleContract(unittest.TestCase):
    def test_no_gradle_files_or_directories(self):
        offenders = [str(path) for path in tracked_paths() if GRADLE.search(str(path))]
        # The policy test's own name is the only permitted Gradle-named file.
        offenders = [path for path in offenders if path != "tests/test_no_gradle_contract.py"]
        self.assertEqual([], offenders, "Forbidden Gradle paths remain")

    def test_no_gradle_in_executable_sources_or_configuration(self):
        offenders = []
        for relative in tracked_paths():
            if relative.parts[0] in {"docs", "tests"} or relative.suffix not in SOURCE_SUFFIXES:
                continue
            path = ROOT / relative
            if not path.is_file():
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if GRADLE.search(line):
                    offenders.append(f"{relative}:{line_number}")
        self.assertEqual([], offenders, "Forbidden Gradle integration remains")


if __name__ == "__main__":
    unittest.main()
