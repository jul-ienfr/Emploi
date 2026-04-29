import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_python_module_entrypoint_supports_version():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "emploi.cli", "--version"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip()


def test_packaged_console_script_supports_version_after_editable_install(tmp_path):
    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    python = venv_dir / "bin" / "python"
    emploi = venv_dir / "bin" / "emploi"
    subprocess.run([str(python), "-m", "pip", "install", "-e", ".[dev]"], cwd=PROJECT_ROOT, check=True)

    result = subprocess.run(
        [str(emploi), "--version"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip()
