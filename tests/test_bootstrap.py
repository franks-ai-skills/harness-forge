"""Bootstrap imports must work without user plugins or site packages."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_has_no_personal_plugin_dependency(self):
        source = Path(__file__).resolve().parents[1] / "src"
        with tempfile.TemporaryDirectory(prefix="forge-bootstrap-") as config_dir:
            task_environment = os.environ.copy()
            task_environment["XDG_CONFIG_HOME"] = config_dir
            task_environment["CLAUDE_CONFIG_DIR"] = config_dir
            import_result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    f"import sys; sys.path.insert(0, {str(source)!r}); "
                    "import harness_forge; print(harness_forge.__file__)",
                ],
                cwd=config_dir,
                env=task_environment,
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(import_result.returncode, 0, import_result.stderr)
        self.assertEqual(
            Path(import_result.stdout.strip()), source / "harness_forge/__init__.py"
        )


if __name__ == "__main__":
    unittest.main()
