import io
import json
import os
import subprocess
import sys
from distutils.version import LooseVersion
from hashlib import md5
from pathlib import Path
from typing import Any, List, Optional
import click

from ensureconda.api import (determine_conda_version, determine_mamba_version, determine_micromamba_version)
from ensureconda.installer import extract_files_from_conda_package, install_micromamba, request_url_with_retry
from ensureconda.resolve import (conda_executables, conda_standalone_executables, mamba_executables,
                                 micromamba_executables, platform_subdir, safe_next)

MIN_CONDA_VERSION = LooseVersion("4.10")
MIN_MAMBA_VERSION = LooseVersion("0.15")

_exe: Optional[Path] = None
_envs_dir = None
_env_name = None


def install_conda_exe() -> Optional[Path]:
    url = "https://api.anaconda.org/package/conda-forge/conda-standalone/files"
    resp = request_url_with_retry(url)

    candidates = []
    for file_info in resp.json():
        if file_info["attrs"]["subdir"] == system_platform():
            candidates.append(file_info)

    if len(candidates) == 0:
        return

    chosen = max(candidates,
                 key=lambda attrs: (
                     LooseVersion(attrs["version"]),
                     attrs["attrs"]["build_number"],
                     attrs["attrs"]["timestamp"],
                 ))
    url = chosen["download_url"]
    if url.startswith("//"):
        url = f"https:{url}"
    resp = request_url_with_retry(url)

    tarball = io.BytesIO(resp.content)

    return extract_files_from_conda_package(
        tarball=tarball,
        filename="standalone_conda/conda.exe",
        dest_filename="conda_standalone",
    )


def is_conda(exe: Path, standalone: Optional[bool] = None) -> bool:
    if standalone:
        return exe.name == "conda_standalone"
    if standalone is None:
        return exe.name in ["conda", "conda_standalone"]
    return exe.name == "conda"


def is_mamba(exe: Path) -> bool:
    return exe.name == "mamba"


def is_micromamba(exe: Optional[Path] = None) -> bool:
    exe = exe or _exe
    return exe is not None and exe.name == "micromamba"


def conda_exe(standalone: Optional[bool] = None, install: bool = True):
    global _exe
    if _exe and is_conda(_exe, standalone=standalone):
        return _exe

    if standalone is not True:
        for exe in conda_executables():
            if determine_conda_version(exe) >= MIN_CONDA_VERSION:
                return Path(exe)

    if standalone is not False:
        for exe in conda_standalone_executables():
            if determine_conda_version(exe) >= MIN_CONDA_VERSION:
                return Path(exe)

        if install:
            exe = install_conda_exe()
            if exe and determine_conda_version(exe) >= MIN_CONDA_VERSION:
                return Path(exe)


def mamba_exe():
    global _exe
    if _exe and is_mamba(_exe):
        return _exe

    for exe in mamba_executables():
        if determine_mamba_version(exe) >= MIN_MAMBA_VERSION:
            return Path(exe)


def micromamba_exe(install: bool = True):
    global _exe
    if _exe and is_micromamba(_exe):
        return _exe

    for exe in micromamba_executables():
        if determine_micromamba_version(exe) >= MIN_MAMBA_VERSION:
            return Path(exe)

    if install:
        exe = install_micromamba()
        if exe and determine_micromamba_version(exe) >= MIN_MAMBA_VERSION:
            return Path(exe)


def system_exe(conda: Optional[bool] = None,
               conda_standalone: Optional[bool] = None,
               mamba: Optional[bool] = None,
               micromamba: Optional[bool] = None) -> Path:
    global _exe
    if _exe:
        assert conda is None and mamba is None and micromamba is None
        return _exe

    # conda_standalone can stay None => optional
    if conda or conda_standalone or mamba or micromamba:
        conda = conda or False
        mamba = mamba or False
        micromamba = micromamba or False
    else:
        conda, mamba, micromamba = True, True, True

    if not _exe and conda:
        _exe = conda_exe(standalone=False)
    if not _exe and conda_standalone is not False:
        _exe = conda_exe(standalone=True)
    if not _exe and mamba:
        _exe = mamba_exe()
    if not _exe and micromamba:
        _exe = micromamba_exe()

    if not _exe:
        click.secho("No valid Conda executable was found", fg="red")
        exit(1)
    return _exe


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

    conda_prefix = os.getenv("CONDA_PREFIX", os.getenv("MAMBA_ROOT_PREFIX"))
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
