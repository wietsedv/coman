from collections import OrderedDict
from distutils.version import LooseVersion
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from glob import glob
from typing import Iterator, List, Optional
import click

import ruamel.yaml as yaml

from conda_lock.conda_lock import create_lockfile_from_spec
from conda_lock.src_parser import LockSpecification

from coman.spec import (conda_lock_file, conda_outdated, edit_spec_file, conda_lock_hash, pip_lock_file, pip_lock_hash,
                        pip_outdated, require_spec_file, spec_file, spec_package_names, spec_pip_requirements,
                        spec_platforms)
from coman.system import (env_prefix, conda_search, pypi_search, run_exe, system_exe)


def env_python_exe():
    return env_prefix() / "bin" / "python"


def env_python_version():
    vstring = subprocess.check_output([env_python_exe(), "--version"], encoding="utf-8").split(" ")[-1]
    return LooseVersion(vstring)


def filter_platform_selectors(content: str, platform) -> Iterator[str]:
    platform_sel = {
        "linux-64": {"linux64", "unix", "linux"},
        "linux-aarch64": {"aarch64", "unix", "linux"},
        "linux-ppc64le": {"ppc64le", "unix", "linux"},
        "osx-64": {"osx", "osx64", "unix"},
        "osx-arm64": {"arm64", "osx", "unix"},
        "win-64": {"win", "win64"},
    }

    # This code is adapted from conda-build
    sel_pat = re.compile(r"(.+?)\s*(#.*)?\[([^\[\]]+)\](?(2)[^\(\)]*)$")
    for line in content.splitlines(keepends=False):
        if line.lstrip().startswith("#"):
            continue
        m = sel_pat.match(line)
        if m:
            cond = m.group(3)
            if cond == platform or cond in platform_sel[platform]:
                yield line
        else:
            yield line


def parse_environment_file(spec_file: Path, platform: str) -> LockSpecification:
    with spec_file.open("r") as f:
        filtered_content = "\n".join(filter_platform_selectors(f.read(), platform=platform))
        env_yaml_data = yaml.safe_load(filtered_content)

    specs = [x for x in env_yaml_data["dependencies"] if isinstance(x, str)]
    channels = env_yaml_data.get("channels", [])

    return LockSpecification(specs=specs, channels=channels, platform=platform)


def _env_lock_conda():
    platforms = spec_platforms()
    new_lock_paths = [str(conda_lock_file(p)) for p in platforms]
    for lock_path in glob(str(conda_lock_file("*"))):
        if lock_path not in new_lock_paths:
            os.remove(lock_path)

    for platform in platforms:
        print(f"Generating lock file for {platform}", file=sys.stderr)
        lock_spec = parse_environment_file(spec_file(), platform)
        lock_contents = create_lockfile_from_spec(
            channels=lock_spec.channels,
            conda=system_exe(),
            spec=lock_spec,
            kind="explicit",
        )
        with open(conda_lock_file(platform), "w") as f:
            f.write("\n".join(lock_contents) + "\n")


def _run_pip_compile(requirements):
    pin_args = [
        sys.executable,
        "-m",
        "piptools",
        "compile",
        "-",
        "-o",
        "-",
        "--no-allow-unsafe",
        "--generate-hashes",
        "--no-header",
    ]
    res = subprocess.run(
        pin_args,
        input=requirements,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    if res.returncode != 0:
        print(res.stderr)
        exit(1)
    return res.stdout


def _env_lock_pip():
    requirements = spec_pip_requirements()
    if not requirements:
        if pip_lock_file().exists():
            print("Removing Pip lock file", file=sys.stderr)
            os.remove(pip_lock_file())
        return

    print("Generating lock file for pip", file=sys.stderr)
    lock = _run_pip_compile(requirements)

    lock_hash = hashlib.sha256(lock.encode("utf-8")).hexdigest()
    lock = f"# Generated by pip-compile.\n# env_hash: {lock_hash}\n\n{lock}"
    with open(pip_lock_file(), "w") as f:
        f.write(lock)


def env_lock(conda: bool = True, pip: bool = True):
    require_spec_file()
    if conda:
        _env_lock_conda()
    if pip:
        _env_lock_pip()


def _env_install_conda(prune: bool):
    print("Installing Conda environment" + " [prune]" if prune else "", file=sys.stderr)
    lock_path = conda_lock_file()
    prefix = env_prefix()
    args = [
        "create" if prune or not prefix.exists() else "update",
        "--file",
        lock_path,
        "--prefix",
        prefix,
        "--yes",
    ]
    if not run_exe(args):
        print(f"\nCould not install {lock_path} into {prefix}", file=sys.stderr)
        exit(1)


def _env_install_pip():
    print("Installing Pip packages", file=sys.stderr)
    args = [
        env_python_exe(),
        "-m",
        "pip",
        "install",
        "-r",
        "requirements.txt",
        "--no-deps",
        "--disable-pip-version-check",
        "--no-input",
        "--quiet",
    ]
    res = subprocess.run(args)
    if res.returncode != 0:
        print(res.stderr)
        exit(1)


def env_install(prune: Optional[bool] = None, force: bool = False):
    require_spec_file()

    use_pip = bool(spec_pip_requirements())
    if not conda_lock_file().exists() or (use_pip and not pip_lock_file().exists()):
        env_lock()

    conda_hash = conda_lock_hash()
    conda_changed = conda_outdated(conda_hash)

    pip_hash = pip_lock_hash()
    pip_changed = pip_outdated(pip_hash)

    if prune is None:
        prune = (use_pip and conda_changed) or pip_changed

    # Conda
    if force or prune or conda_changed:
        _env_install_conda(prune)
        with open(env_prefix() / "conda_hash.txt", "w") as f:
            f.write(conda_hash)
    else:
        print("Conda environment is already up-to-date", file=sys.stderr)

    # Pip
    pip_hash_path = env_prefix() / "pip_hash.txt"
    if pip_hash:
        if force or prune or pip_changed:
            _env_install_pip()
            with open(pip_hash_path, "w") as f:
                f.write(pip_hash)
        else:
            print("Pip packages are already up-to-date", file=sys.stderr)
    elif pip_hash_path.exists():
        os.remove(pip_hash_path)


def env_uninstall():
    run_exe(["env", "remove", "--prefix", env_prefix()])


def change_spec(add_pkgs: List[str] = [], remove_pkgs: List[str] = [], prune: Optional[bool] = None, pip: bool = False):
    require_spec_file()
    spec_data, save_spec_file = edit_spec_file()

    def _dep_names(deps):
        return [pkg.split(" ")[0] if not isinstance(pkg, OrderedDict) else None for pkg in deps]

    deps = spec_data["dependencies"]
    dep_names = _dep_names(deps)

    def _add_pkg(pkg: str, pip: bool):
        if pip:
            pkg_info = pypi_search(pkg)
        else:
            pkg_info = conda_search(pkg, spec_data["channels"])

        name = pkg_info["name"]
        pkg = f"{name} >={pkg_info['version']}"

        i = len(deps)
        if name in dep_names:
            i = dep_names.index(name)
            if deps[i] == pkg:
                return False
            deps.pop(i)

        deps.insert(i, pkg)
        dep_names.insert(i, name)
        print(f"Added '{pkg}' to spec", file=sys.stderr)
        return True

    def _remove_pkg(pkg: str):
        if pkg not in dep_names:
            print(f"Dependency {pkg} not found", file=sys.stderr)
            return False

        i = dep_names.index(pkg)
        pkg = deps.pop(i)
        dep_names.pop(i)
        print(f"Removed '{pkg}' from spec", file=sys.stderr)
        return True

    if pip:
        if "pip" not in dep_names:
            _add_pkg("pip", pip=False)
        pip_deps = None
        for spec in deps:
            if isinstance(spec, OrderedDict) and "pip" in spec:
                if not spec["pip"]:
                    spec["pip"] = []
                pip_deps = spec["pip"]
                break
        if pip_deps is None:
            deps.append(dict(pip=[]))
            pip_deps = deps[-1]["pip"]

        deps = pip_deps
        dep_names = _dep_names(deps)

    changed = False
    for pkg in add_pkgs:
        if _add_pkg(pkg, pip=pip):
            changed = True
    for pkg in remove_pkgs:
        if _remove_pkg(pkg):
            changed = True

    if changed:
        save_spec_file()
        env_lock(conda=not pip)

    if (changed or prune) and env_prefix().exists():
        env_install(prune=prune, force=False)


def env_show(query: List[str]):
    out = run_exe(["list", "--prefix", env_prefix(), *query, "--json"])
    if not out:
        print("No results", file=sys.stderr)
        exit(1)
    res = json.loads(out)

    conda_names, pip_names = spec_package_names()
    for pkg_info in res:
        name, version, channel = pkg_info["name"], pkg_info["version"], pkg_info["channel"]

        fg, warning = None, ""
        if channel == "pypi":
            if name in pip_names:
                fg = "blue"
            else:
                warning = "WARNING: implicit pip dependency"
            if name in conda_names:
                warning = "WARNING: conda dependency overriden by pip"
        else:
            if name in conda_names:
                fg = "green"

        name_fmt = click.style(name.ljust(20), fg=fg, bold=True)
        version_fmt = click.style(version.ljust(10), fg=fg)
        channel_fmt = click.style(channel.ljust(14), fg="bright_black")
        warning_str = click.style(warning.ljust(20), fg="yellow")

        line = f"{name_fmt} {version_fmt} {channel_fmt} {warning_str}"
        click.echo(line)
