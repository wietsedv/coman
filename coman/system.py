import io
import json
import os
import subprocess
import sys
from distutils.version import LooseVersion
from hashlib import md5
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
from ensureconda.api import determine_conda_version, determine_mamba_version, determine_micromamba_version
from ensureconda.installer import extract_files_from_conda_package, install_micromamba, request_url_with_retry
from ensureconda.resolve import (conda_executables, conda_standalone_executables, mamba_executables,
                                 micromamba_executables, platform_subdir, safe_next)
from semantic_version.base import Version

MIN_CONDA_VERSION = LooseVersion("4.10")
MIN_MAMBA_VERSION = LooseVersion("0.15")


class Environment:
    def __init__(self, conda: 'Conda'):
        cwd = os.path.normpath(os.getcwd())
        hash = md5(cwd.encode("utf-8")).hexdigest()[:8]

        self.platform = platform_subdir()
        self.conda = conda
        self.name = f"{os.path.basename(cwd)}-{hash}"

    @property
    def prefix(self):
        return self.conda.envs_dir / self.name

    @property
    def python(self):
        return self.prefix / "bin" / "python"

    @property
    def python_version(self):
        if self.python.exists():
            vstr = subprocess.check_output([self.python, "--version"], encoding="utf-8").split(" ")[-1].strip()
            return Version(vstr)

    @property
    def conda_hash(self):
        env_hash_file = self.prefix / "conda_hash.txt"
        if env_hash_file.exists():
            with open(env_hash_file) as f:
                return f.read().strip()

    @property
    def pip_hash(self):
        env_hash_file = self.prefix / "pip_hash.txt"
        if env_hash_file.exists():
            with open(env_hash_file) as f:
                return f.read().strip()

    def shell_hook(self, shell_type: str):
        exe_flag = " --micromamba" if self.conda.is_micromamba() else ""
        bin_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        return f"eval $({bin_dir}/coman{exe_flag} shell --shell {shell_type} --hook --quiet)"

    def run(self, args: List[str]):
        # "conda run" only works with regular conda
        # if self.conda.is_conda(standalone=False):
        #     p = subprocess.run([self.conda.exe, "run", "--prefix", self.conda.env.prefix, "--no-capture-out", "--live-stream", *args])
        #     exit(p.returncode)
        print(click.style("Conda:", fg="green"), click.style(self.name, fg="blue") + "\n", file=sys.stderr)

        if os.getenv("COMAN_ACTIVE"):
            exit(subprocess.run(args).returncode)

        cmd = " ".join(args)
        exit(
            subprocess.run([
                "/usr/bin/env",
                "bash",
                "-c",
                f"{self.shell_hook('bash')} && {cmd} && exit 0",
            ]).returncode)


class Conda:
    _exe = None

    def __init__(self,
                 mamba: Optional[bool] = None,
                 conda: Optional[bool] = None,
                 conda_standalone: Optional[bool] = None,
                 micromamba: Optional[bool] = None) -> None:
        if mamba or conda or conda_standalone or micromamba:
            self.mamba = mamba or False
            self.conda = conda or False
            self.conda_standalone = conda_standalone or False
            self.micromamba = micromamba or False
        else:
            self.mamba, self.conda, self.conda_standalone, self.micromamba = True, True, True, True

        self.root = self._get_root()

        os.environ["CONDA_ENVS_PATH"] = os.getenv("CONDA_ENVS_PATH", f"{self.root}/envs")
        self.envs_dir = Path(os.environ["CONDA_ENVS_PATH"])

        os.environ["CONDA_PKGS_DIRS"] = os.getenv("CONDA_PKGS_DIRS", f"{self.root}/pkgs")
        self.pkgs_dir = Path(os.environ["CONDA_PKGS_DIRS"])

        self.env = Environment(self)

    @property
    def exe(self):
        if self._exe:
            return self._exe
        if not self._exe and self.mamba:
            self._exe = mamba_exe()
        if not self._exe and self.conda:
            self._exe = conda_exe()
        if not self._exe and self.conda_standalone:
            self._exe = conda_standalone_exe()
        if not self._exe and self.micromamba:
            self._exe = micromamba_exe()
        if not self._exe:
            click.secho("No valid Conda executable was found", fg="red", file=sys.stderr)
            exit(1)
        return self._exe

    def run(self, args: List[Any], capture: bool = True, check: bool = True, exe: Optional[Path] = None):
        exe = exe or self.exe
        args = [exe, *args]
        if not capture:
            return subprocess.run(args, encoding="utf-8")

        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
        if check and p.returncode != 0:
            if p.stdout:
                print(p.stdout.strip(), file=sys.stderr)
            print(p.stderr.strip(), file=sys.stderr)
        return p

    def is_mamba(self) -> bool:
        return self.exe.name == "mamba"

    def is_conda(self, standalone: Optional[bool] = None) -> bool:
        if standalone:
            return self.exe.name == "conda_standalone"
        if standalone is None:
            return self.exe.name in ["conda", "conda_standalone"]
        return self.exe.name == "conda"

    def is_micromamba(self) -> bool:
        return self.exe.name == "micromamba"

    def _get_root(self):
        root = os.getenv("MAMBA_ROOT_PREFIX", os.getenv("CONDA_ROOT"))
        if root:
            _conda_root = Path(root)
            return _conda_root

        p = self.run(["info", "--json"], check=False)
        if p.returncode == 0:
            res = json.loads(p.stdout)
            _conda_root = Path(res["default_prefix"])
            return _conda_root

        _conda_root = Path(os.path.expanduser("~/conda"))
        return _conda_root


def install_conda_standalone() -> Optional[Path]:
    url = "https://api.anaconda.org/package/conda-forge/conda-standalone/files"
    resp = request_url_with_retry(url)

    candidates = []
    for file_info in resp.json():
        if file_info["attrs"]["subdir"] == platform_subdir():
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


def mamba_exe():
    for exe in mamba_executables():
        if determine_mamba_version(exe) >= MIN_MAMBA_VERSION:
            return Path(exe)


def conda_exe():
    for exe in conda_executables():
        if determine_conda_version(exe) >= MIN_CONDA_VERSION:
            return Path(exe)


def conda_standalone_exe(install: bool = True):
    for exe in conda_standalone_executables():
        if determine_conda_version(exe) >= MIN_CONDA_VERSION:
            return Path(exe)

    if install:
        exe = install_conda_standalone()
        if exe and determine_conda_version(exe) >= MIN_CONDA_VERSION:
            return Path(exe)


def micromamba_exe(install: bool = True):
    for exe in micromamba_executables():
        if determine_micromamba_version(exe) >= MIN_MAMBA_VERSION:
            return Path(exe)

    if install:
        exe = install_micromamba()
        if exe and determine_micromamba_version(exe) >= MIN_MAMBA_VERSION:
            return Path(exe)


def conda_search(conda: Conda, pkg: str, channels: List[str], platform: Optional[str] = None) -> List[Dict[str, Any]]:
    args = []
    for c in channels:
        args.extend(["-c", c])
    if platform:
        args.extend(["--subdir", platform])

    p = conda.run(["search", pkg, *args, "--json"], check=False, exe=conda_exe())
    if not p.stdout:
        print(f"Unable to query package through '{conda.exe}'", file=sys.stderr)
        exit(1)
    res = json.loads(p.stdout)
    if "error" in res:
        print(res["error"], file=sys.stderr)
        exit(1)

    if pkg not in res:
        click.secho(f"Package '{pkg}' not found. Did you mean: {', '.join(sorted(res))}", fg="yellow", file=sys.stderr)
        exit(1)

    info = res[pkg]
    for pkg_info in info:
        pkg_info["platform"] = pkg_info["subdir"]
        pkg_info["channel"] = pkg_info["channel"].split("/")[-2]

    return info


def conda_pkg_info(conda: Conda, pkg: str, channels: List[str]):
    return conda_search(conda, pkg, channels)[-1]


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
            condastandalone_exe = install_conda_standalone()
            condastandalone_ver = determine_conda_version(condastandalone_exe) if condastandalone_exe else None
            condastandalone_state = "unsupported" if condastandalone_ver and condastandalone_ver < MIN_CONDA_VERSION else "ok"
        condastandalone_ver = f"{condastandalone_ver} ({condastandalone_state}) [{condastandalone_exe}]" if condastandalone_ver else "n/a"
    except IndexError:
        condastandalone_ver = "n/a"
    print(f"> Conda standalone: {condastandalone_ver}")

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
