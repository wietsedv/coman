import json
import os
import subprocess
import sys
from distutils.version import LooseVersion
from hashlib import md5
from os import PathLike
from pathlib import Path
from typing import Any, List, Optional

from ensureconda import ensureconda
from ensureconda.resolve import platform_subdir

MIN_CONDA_VERSION = LooseVersion("4.9")
MIN_MAMBA_VERSION = LooseVersion("0.15")

_envs_dir = None
_env_name = None
_exe: Optional[PathLike] = None


def envs_dir():
    global _envs_dir
    if _envs_dir:
        return _envs_dir
    res = json.loads(run_exe(["info", "--json"]))
    _envs_dir = Path(res["envs_dirs"][0])
    return _envs_dir


def env_name():
    global _env_name
    if _env_name:
        return _env_name
    current_dir = os.path.normpath(os.getcwd())
    current_basename = os.path.basename(current_dir)
    hash = md5(current_dir.encode("utf-8")).hexdigest()[:8]
    _env_name = f"{current_basename}-{hash}"
    return _env_name


def env_prefix():
    return envs_dir() / env_name()


def env_prefix_hash(prefix: Path) -> Optional[str]:
    env_hash_file = prefix / "env_hash.txt"
    if env_hash_file.exists():
        with open(env_hash_file) as f:
            return f.read().strip()


def is_mamba(exe: Path) -> bool:
    return exe.name == "mamba"


def is_micromamba(exe: Path) -> bool:
    return exe.name == "micromamba"


def is_conda(exe: Path) -> bool:
    return exe.name == "conda"


def is_conda_standalone(exe: Path) -> bool:
    return exe.name == "conda_standalone"


def system_exe() -> Path:
    global _exe
    if not _exe:
        _exe = ensureconda(
            mamba=True,
            micromamba=False,
            conda=True,
            conda_exe=True,
            min_mamba_version=MIN_MAMBA_VERSION,
            min_conda_version=MIN_CONDA_VERSION,
        )
        if not _exe:
            raise RuntimeError("No valid conda installation was found")
    return Path(_exe)


def run_exe(args: List[Any]):
    args = [str(a) for a in [system_exe(), *args]]
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
    if p.returncode != 0:
        if p.stdout:
            print(p.stdout.strip(), file=sys.stderr)
        print(p.stderr.strip(), file=sys.stderr)
    return p.stdout


def system_platform():
    return platform_subdir()


def repoquery_search(pkg: str, channels: List[str]):
    args = []
    for c in channels:
        args.extend(["-c", c])
    res = json.loads(run_exe(["repoquery", "search", pkg, *args, "--json"]))["result"]
    if res["msg"]:
        print(res["msg"])
        exit(1)
    pkg_ = max(res["pkgs"], key=lambda p: p["timestamp"])
    return pkg_
