"""Locating and running the external programs the pipeline drives."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# scripts/fetch_osmnexus.sh installs the pinned OSMnexus release here.
DEFAULT_OSMNEXUS = REPO / ".tools" / "osmnexus" / "osmnexus"
DEFAULT_OSMNEXUS_CONFIG = REPO / ".tools" / "osmnexus" / "config"


def osmium():
    path = shutil.which("osmium")
    if not path:
        raise SystemExit("osmium (osmium-tool) not found on PATH — apt install osmium-tool")
    return path


def osmnexus():
    path = Path(os.environ.get("OSMNEXUS_BIN", DEFAULT_OSMNEXUS))
    if not path.is_file():
        raise SystemExit(f"OSMnexus binary not found at {path} — run scripts/fetch_osmnexus.sh")
    return str(path)


def osmnexus_config():
    path = Path(os.environ.get("OSMNEXUS_CONFIG", DEFAULT_OSMNEXUS_CONFIG))
    if not (path / "bikelanes" / "topic.json").is_file():
        raise SystemExit(f"OSMnexus bikelanes config not found in {path} — run scripts/fetch_osmnexus.sh")
    return str(path)


def manby():
    # Installed as a console script into the same environment as this package.
    candidate = Path(sys.executable).parent / "manby"
    if candidate.is_file():
        return str(candidate)
    path = shutil.which("manby")
    if not path:
        raise SystemExit("manby not found — uv sync installs it")
    return path


def run(cmd, **kwargs):
    """Run a command, echoing it, and fail with its output if it fails."""
    printable = " ".join(str(c) for c in cmd)
    print(f"$ {printable}", flush=True)
    result = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        sys.stderr.write(result.stdout[-4000:])
        sys.stderr.write(result.stderr[-4000:])
        raise SystemExit(f"command failed ({result.returncode}): {printable}")
    return result
