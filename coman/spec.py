from coman.system import env_prefix_conda_hash, env_prefix_pip_hash, system_platform
import re
from pathlib import Path
import sys
from typing import Callable, List, Optional, Tuple

import ruamel.yaml as yaml
import click

ENV_HASH_PATTERN = re.compile(r"^# env_hash: (.*)$")
PLATFORM_PATTERN = re.compile(r"^# platform: (.*)$")

PLATFORMS = ["linux-64", "linux-aarch64", "linux-ppc64le", "osx-64", "osx-arm64", "win-64"]


def spec_file():
    return Path("environment.yml")


def require_spec_file():
    spec_path = spec_file()
    if not spec_path.exists():
        print(f"Specification file `{spec_file()}` is not found in the current directory. Create it with `coman init`",
              file=sys.stderr)
        exit(1)
    return spec_path


def spec_pip_requirements() -> Optional[str]:
    with open(require_spec_file()) as f:
        env = yaml.safe_load(f)

    for pkg in env["dependencies"]:
        if isinstance(pkg, dict) and "pip" in pkg:
            pip_pkgs = []
            for pkg in pkg["pip"]:
                _, ver = pkg.split(" ")
                if ver.startswith("http"):
                    pip_pkgs.append(ver)
                else:
                    pip_pkgs.append(pkg)
            return "\n".join(pip_pkgs)
    return None


def spec_package_names() -> Tuple[List[str], List[str]]:
    with open(require_spec_file()) as f:
        env = yaml.safe_load(f)

    conda_names, pip_names = [], []
    for pkg in env["dependencies"]:
        if isinstance(pkg, str):
            conda_names.append(pkg.split(" ")[0])
        if isinstance(pkg, dict) and "pip" in pkg:
            for pip_pkg in pkg["pip"]:
                pip_names.append(pip_pkg.split(" ")[0])
    return conda_names, pip_names


def spec_dependencies() -> Tuple[List[dict], List[dict]]:
    with open(require_spec_file()) as f:
        env = yaml.round_trip_load(f)

    conda_comments = env["dependencies"].ca.items
    conda_specs, pip_specs = [], []
    for i, pkg in enumerate(env["dependencies"]):
        if isinstance(pkg, str):
            name, ver = pkg.split()
            comment = conda_comments[i][0].value.strip() if i in conda_comments else ""
            conda_specs.append({"name": name, "version": ver, "comment": comment})
            continue

        if isinstance(pkg, dict) and "pip" in pkg:
            pip_comments = env["dependencies"][i]["pip"].ca.items
            for j, pip_pkg in enumerate(pkg["pip"]):
                if isinstance(pip_pkg, str):
                    name, ver = pip_pkg.split()
                    comment = pip_comments[j][0].value.strip() if j in pip_comments else ""
                    pip_specs.append({"name": name, "channel": "pypi", "version": ver, "comment": comment})

    return conda_specs, pip_specs


def _spec_load_list(key: str, item_key: str, default: List[dict] = []) -> List[dict]:
    if not spec_file().exists():
        return default

    with open(spec_file()) as f:
        env = yaml.round_trip_load(f)

    if key not in env:
        return default

    comments = env[key].ca.items
    items = []
    for i, item in enumerate(env[key]):
        comment = comments[i][0].value.strip() if i in comments else ""
        items.append({item_key: item, "comment": comment})
    return items



def spec_platforms() -> List[dict]:
    sys_platform = system_platform()
    platforms = _spec_load_list("platforms", "platform", default=[{"platform": sys_platform}])
    return platforms


def spec_platform_names() -> List[str]:
    sys_platform = system_platform()
    with open(require_spec_file()) as f:
        env = yaml.safe_load(f)
    platform_names = env.get("platforms", [sys_platform])
    if sys_platform not in platform_names:
        click.secho(f"WARNING: Platform {sys_platform} is not whitelisted in {spec_file()}\n",
                    fg="yellow",
                    file=sys.stderr)
    return platform_names


def spec_channels() -> List[dict]:
    return _spec_load_list("channels", "channel", default=[{"channel": "conda-forge"}])


def spec_channel_names() -> List[str]:
    spec_path = require_spec_file()
    with open(spec_path) as f:
        env = yaml.safe_load(f)
    return env.get("channels", ["conda-forge"])


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


def pip_lock_comments():
    lock_path = pip_lock_file()
    if not lock_path.exists():
        return {}

    comments = {}
    with open(lock_path) as f:
        name, comment = None, ""
        for line in f:
            if name is not None:
                if line.startswith("    --hash="):
                    continue
                if line == "    # via -r -\n":
                    continue
                if line.startswith("    # "):
                    comment += " " + line[6:].strip()
                    continue
                if comment:
                    comments[name] = comment
                name = None
            if name is None:
                if line[0] in "#\n":
                    continue
                if "==" in line:
                    name, comment = line.split("==")[0], ""
                    continue
        if comment:
            comments[name] = comment

    return comments


def pip_outdated(pip_hash: Optional[str] = None):
    pip_hash = pip_hash or pip_lock_hash()
    return env_prefix_pip_hash() != pip_hash


def edit_spec_file() -> Tuple[dict, Callable]:
    spec_path = require_spec_file()

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
