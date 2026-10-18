"""Install, build and run only this experiment; leave product dependencies alone."""

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import sysconfig
import venv

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--require-windows-arm64", action="store_true")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "CGO_ENABLED": "0",
        "GOTOOLCHAIN": "local",
        "PAIRING_POC_OUTPUT": str(output),
    }

    def run(label, command):
        print(f"{label}...", flush=True)
        with (output / f"{label}.txt").open("w", encoding="utf-8") as log:
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        print((output / f"{label}.txt").read_text(encoding="utf-8"), flush=True)
        result.check_returncode()

    info = {
        "python": sys.version,
        "python_platform": sysconfig.get_platform(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "go_version": subprocess.check_output(
            ["go", "version"], env=env, text=True
        ).strip(),
        "go_host": subprocess.check_output(
            ["go", "env", "GOHOSTOS", "GOHOSTARCH", "GOOS", "GOARCH"],
            env=env,
            text=True,
        ).splitlines(),
    }
    (output / "environment.json").write_text(
        json.dumps(info, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(info, indent=2), flush=True)
    if args.require_windows_arm64:
        if info["python_platform"] != "win-arm64" or info["go_host"] != [
            "windows",
            "arm64",
            "windows",
            "arm64",
        ]:
            raise SystemExit("Native Windows ARM64 Python and Go are required")

    venv_dir = ROOT / ".venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # Source builds are intentional: Windows ARM64 may lack crypto wheels.
    # CI already supplies OpenSSL for this existing Arbiter dependency.
    run(
        "install",
        [str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
    )
    run("dependencies", [str(python), "-m", "pip", "freeze"])
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    peer = build / ("peer.exe" if os.name == "nt" else "peer")
    run("build", ["go", "build", "-buildvcs=false", "-o", str(peer), "."])
    run("binary", ["go", "version", "-m", str(peer)])
    run("vet", ["go", "vet", "."])
    run("tests", [str(python), str(ROOT / "check.py")])


if __name__ == "__main__":
    main()
