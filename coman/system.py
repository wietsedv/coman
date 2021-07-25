import itertools
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
from ensureconda.resolve import conda_executables, conda_standalone_executables, platform_subdir, safe_next

MIN_CONDA_VERSION = LooseVersion("4.9")
MIN_MAMBA_VERSION = LooseVersion("0.15")

_exe: Optional[Path] = None
_envs_dir = None
_env_name = None


def system_exe(mamba: bool = None, micromamba: bool = None, conda: bool = None) -> Path:
    global _exe

    if _exe:
        assert mamba is None and micromamba is None and conda is None
        return _exe

    if mamba or micromamba or conda:
        assert mamba is not None and micromamba is not None and conda is not None
    else:
        mamba, micromamba, conda = True, False, True

    e = ensureconda(
        mamba=mamba,
        micromamba=micromamba,
        conda=conda,
        conda_exe=conda,
        min_mamba_version=MIN_MAMBA_VERSION,
        min_conda_version=MIN_CONDA_VERSION,
    )
    if not e:
        raise RuntimeError("No valid conda installation was found")
    _exe = Path(e)
    return _exe


def conda_exe(standalone: bool = True):
    global _exe
    if _exe and is_conda(Path(_exe), standalone=standalone):
        return _exe

    conda_iter = conda_executables()
    if standalone:
        conda_iter = itertools.chain(conda_iter, conda_standalone_executables())
    exe = safe_next(conda_iter)
    if exe:
        return Path(exe)


def run_exe(args: List[Any], check: bool = True):
    args = [str(a) for a in [system_exe(), *args]]
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
    if check and p.returncode != 0:
        if p.stdout:
            print(p.stdout.strip(), file=sys.stderr)
        print(p.stderr.strip(), file=sys.stderr)
        return
    return p.stdout


def envs_dir():
    global _envs_dir
    if _envs_dir:
        return _envs_dir

    root = os.getenv("COMAN_ENVS_ROOT")
    if root:
        _envs_dir = Path(root)
        return _envs_dir

    conda_prefix = os.getenv("MAMBA_ROOT_PREFIX", os.getenv("CONDA_PREFIX"))
    if conda_prefix:
        _envs_dir = Path(conda_prefix) / "envs"
        return _envs_dir

    out = run_exe(["info", "--json"])
    if not out:
        print("Unable to resolve environments directory", file=sys.stderr)
        exit(1)
    res = json.loads(out)
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


def env_prefix_conda_hash() -> Optional[str]:
    env_hash_file = env_prefix() / "conda_hash.txt"
    if env_hash_file.exists():
        with open(env_hash_file) as f:
            return f.read().strip()


def env_prefix_pip_hash() -> Optional[str]:
    env_hash_file = env_prefix() / "pip_hash.txt"
    if env_hash_file.exists():
        with open(env_hash_file) as f:
            return f.read().strip()


def is_mamba(exe: Path) -> bool:
    return exe.name == "mamba"


def is_micromamba(exe: Optional[Path] = None) -> bool:
    exe = exe or _exe
    return exe is not None and exe.name == "micromamba"


def is_conda(exe: Path, standalone: bool = True) -> bool:
    if standalone:
        return exe.name in ["conda", "conda_standalone"]
    return exe.name == "conda"


def system_platform():
    return platform_subdir()


def conda_search(pkg: str, channels: List[str]):
    args = []
    for c in channels:
        args.extend(["-c", c])

    out = run_exe(["search", pkg, *args, "--json"], check=False)
    if not out:
        print(f"Unable to query package through '{system_exe()}'", file=sys.stderr)
        exit(1)
    res = json.loads(out)
    if "error" in res:
        print(res["error"], file=sys.stderr)
        exit(1)

    if pkg not in res:
        print(f"Package '{pkg}' not found. Did you mean: {', '.join(sorted(res))}", file=sys.stderr)
        exit(1)

    pkg_ = max(res[pkg], key=lambda p: p.get("timestamp", 0))
    return pkg_


def pypi_search(pkg: str):
    import urllib3
    http = urllib3.PoolManager()
    data = json.loads(http.request("GET", f"https://pypi.org/pypi/{pkg}/json").data)
    return data["info"]
