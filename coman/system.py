import itertools
import json
import os
import subprocess
import sys
from distutils.version import LooseVersion
from hashlib import md5
from pathlib import Path
from typing import Any, List, Optional

from ensureconda import ensureconda
from ensureconda.api import (determine_conda_version, determine_mamba_version, determine_micromamba_version)
from ensureconda.installer import install_conda_exe, install_micromamba
from ensureconda.resolve import (conda_executables, conda_standalone_executables, mamba_executables,
                                 micromamba_executables, platform_subdir, safe_next)

MIN_CONDA_VERSION = LooseVersion("4.9")
MIN_MAMBA_VERSION = LooseVersion("0.15")

_exe: Optional[Path] = None
_envs_dir = None
_env_name = None


def system_exe(mamba: Optional[bool] = None, micromamba: Optional[bool] = None, conda: Optional[bool] = None) -> Path:
    global _exe
    if _exe:
        assert mamba is None and micromamba is None and conda is None
        return _exe

    if mamba or micromamba or conda:
        mamba = mamba or False
        micromamba = micromamba or False
        conda = conda or False
    else:
        mamba, micromamba, conda = False, False, True

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
    if _exe and is_conda(_exe, standalone=standalone):
        return _exe

    conda_iter = conda_executables()
    if standalone:
        conda_iter = itertools.chain(conda_iter, conda_standalone_executables())
    exe = safe_next(conda_iter)
    if exe:
        return Path(exe)


def micromamba_exe():
    global _exe
    if _exe and is_micromamba(_exe):
        return _exe
    exe = safe_next(micromamba_executables())
    if exe:
        return Path(exe)


def run_exe(args: List[Any], check: bool = True, exe: Optional[Path] = None):
    exe = exe or system_exe()
    args = [str(a) for a in [exe, *args]]
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


def pypi_pkg_info(pkg: str):
    import urllib3
    http = urllib3.PoolManager()
    data = json.loads(http.request("GET", f"https://pypi.org/pypi/{pkg}/json").data)
    info = data["info"]
    info["channel"] = "pypi"
    return info


def conda_info():
    # Mamba
    mamba_exe = safe_next(mamba_executables())
    mamba_ver = "n/a"
    if mamba_exe:
        mamba_ver = determine_mamba_version(mamba_exe)
        mamba_state = "unsupported" if mamba_ver < MIN_MAMBA_VERSION else "ok"
        mamba_ver = f"{mamba_ver} ({mamba_state}) [{mamba_exe}]"
    print(f"> Mamba:            {mamba_ver}")

    # Micromamba
    micromamba_exe = safe_next(micromamba_executables())
    micromamba_ver = determine_micromamba_version(micromamba_exe) if micromamba_exe else None
    micromamba_state = "ok"
    if not micromamba_ver or micromamba_ver < MIN_MAMBA_VERSION:
        micromamba_exe = install_micromamba()
        micromamba_ver = determine_micromamba_version(micromamba_exe) if micromamba_exe else None
        micromamba_state = "unsupported" if micromamba_ver and micromamba_ver < MIN_MAMBA_VERSION else "ok"
    micromamba_ver = f"{micromamba_ver} ({micromamba_state}) [{micromamba_exe}]" if micromamba_ver else "n/a"
    print(f"> Micromamba:       {micromamba_ver}")

    # Conda
    conda_exe = safe_next(conda_executables())
    conda_ver = "n/a"
    if conda_exe:
        conda_ver = determine_conda_version(conda_exe)
        conda_state = "unsupported" if conda_ver < MIN_CONDA_VERSION else "ok"
        conda_ver = f"{conda_ver} ({conda_state}) [{conda_exe}]"
    print(f"> Conda:            {conda_ver}")

    # Conda standalone
    try:
        condastandalone_exe = safe_next(conda_standalone_executables())
        condastandalone_ver = determine_conda_version(condastandalone_exe) if condastandalone_exe else None
        condastandalone_state = "ok"
        if not condastandalone_ver or condastandalone_ver < MIN_CONDA_VERSION:
            condastandalone_exe = install_conda_exe()
            condastandalone_ver = determine_conda_version(condastandalone_exe) if condastandalone_exe else None
            condastandalone_state = "unsupported" if condastandalone_ver and condastandalone_ver < MIN_CONDA_VERSION else "ok"
        condastandalone_ver = f"{condastandalone_ver} ({condastandalone_state}) [{condastandalone_exe}]" if condastandalone_ver else "n/a"
    except IndexError:
        condastandalone_ver = "n/a"
    print(f"> Conda standalone: {condastandalone_ver}")
