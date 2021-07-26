from platform import platform
from coman.system import env_prefix_conda_hash, env_prefix_pip_hash, system_platform
import re
from pathlib import Path
import sys
from typing import Callable, List, Optional, Tuple

import ruamel.yaml as yaml
import click

ENV_HASH_PATTERN = re.compile(r"^# env_hash: (.*)$")
PLATFORM_PATTERN = re.compile(r"^# platform: (.*)$")


def spec_file():
    return Path("environment.yml")


def require_spec_file():
    if not spec_file().exists():
        print(f"Specification file `{spec_file()}` is not found in the current directory. Create it with `coman init`",
              file=sys.stderr)
        exit(1)


def spec_platforms() -> List[str]:
    sys_platform = system_platform()
    if not spec_file().exists():
        return [sys_platform]
    with open(spec_file()) as f:
        env = yaml.safe_load(f)
    platforms = env.get("platforms", [sys_platform])
    if sys_platform not in platforms:
        click.secho(f"WARNING: Platform {sys_platform} is not whitelisted in {spec_file()}\n",
                    fg="yellow",
                    file=sys.stderr)
    return platforms


# def spec_includes_pip():
#     with open(spec_file()) as f:
#         env = yaml.safe_load(f)

#     for pkg in env["dependencies"]:
#         if type(pkg) == str and pkg.split(" ")[0] == "pip":
#             print(pkg["pip"])
#             return True
#     return False


def spec_pip_requirements():
    with open(spec_file()) as f:
        env = yaml.safe_load(f)

    for pkg in env["dependencies"]:
        if isinstance(pkg, dict) and "pip" in pkg:
            return "\n".join(pkg["pip"])
    return None


def spec_package_names() -> Tuple[List[str], List[str]]:
    with open(spec_file()) as f:
        env = yaml.safe_load(f)

    conda_names, pip_names = [], []
    for pkg in env["dependencies"]:
        if isinstance(pkg, str):
            conda_names.append(pkg.split(" ")[0])
        if isinstance(pkg, dict) and "pip" in pkg:
            for pip_pkg in pkg["pip"]:
                pip_names.append(pip_pkg.split(" ")[0])
    return conda_names, pip_names


def spec_channels() -> List[str]:
    with open(spec_file()) as f:
        env = yaml.safe_load(f)
    return env["channels"]


def conda_lock_file(platform: Optional[str] = None):
    platform = platform or system_platform()
    return Path(f"conda-{platform}.lock")


def pip_lock_file():
    return Path("requirements.txt")


def conda_lock_hash() -> str:
    with open(conda_lock_file()) as f:
        for line in f:
            m = ENV_HASH_PATTERN.search(line)
            if m:
                return m.group(1)
        raise RuntimeError("Cannot find env_hash in conda lock file")


def conda_outdated(conda_hash: Optional[str] = None):
    conda_hash = conda_hash or conda_lock_hash()
    return env_prefix_conda_hash() != conda_hash


def pip_lock_hash() -> Optional[str]:
    lock_path = pip_lock_file()
    if not lock_path.exists():
        return None
    with open(lock_path) as f:
        for line in f:
            m = ENV_HASH_PATTERN.search(line)
            if m:
                return m.group(1)
        raise RuntimeError("Cannot find env_hash in pip lock file")


def pip_outdated(pip_hash: Optional[str] = None):
    pip_hash = pip_hash or pip_lock_hash()
    return env_prefix_pip_hash() != pip_hash


def edit_spec_file() -> Tuple[dict, Callable]:
    spec_path = spec_file()
    if not spec_file().exists():
        raise FileNotFoundError(f"{spec_path} not found")

    yaml_ = yaml.YAML()
    with open(spec_path) as f:
        spec_data = yaml_.load(f)

    if "channels" not in spec_data:
        spec_data["channels"] = ["conda-forge"]
    if "dependencies" not in spec_data:
        spec_data["dependencies"] = ["python"]

    def save_func():
        with open(spec_path, "w") as f:
            yaml_.dump(spec_data, f)

    return spec_data, save_func
