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
from typing import Any, Dict, Iterator, List, Optional
import click

import ruamel.yaml as yaml
from conda_lock.conda_lock import create_lockfile_from_spec
from conda_lock.src_parser import LockSpecification

from coman.spec import (conda_lock_file, conda_outdated, edit_spec_file, conda_lock_hash, pip_lock_file, pip_lock_hash,
                        pip_outdated, require_spec_file, spec_channels, spec_file, spec_package_names,
                        spec_pip_requirements, spec_platforms)
from coman.system import (conda_exe, conda_info, conda_root, env_prefix, envs_dir, pkgs_dir, pypi_pkg_info, run_exe,
                          system_exe, system_platform)
from coman._version import __version__

_COL_COLORS = {
    "name": "green",
    "version": "blue",
    "old_version": "red",
    "build": "yellow",
    "channel": "bright_white",
    "pypi": "cyan"
}


def env_python_exe():
    return env_prefix() / "bin" / "python"


def env_python_version():
    vstring = subprocess.check_output([env_python_exe(), "--version"], encoding="utf-8").split(" ")[-1].strip()
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


def env_info():
    print("Current environment")
    sys_status = "up-to-date"
    if not spec_file().exists():
        sys_status = "no environment.yml (run `coman init`)"
    elif not conda_lock_file().exists():
        sys_status = f"no lock file for this platform (run `coman lock`)"
    elif not env_prefix().exists():
        sys_status = "not installed (run `coman install`)"
    elif conda_outdated():
        sys_status = "env outdated (run `coman install`)"
    elif pip_outdated():
        sys_status = "pip outdated (run `coman install`)"

    print(f"> Prefix:   {env_prefix()}")
    print(f"> Platform: {system_platform()}")
    print(f"> Status:   {sys_status}")
    if env_prefix().exists():
        print(f"> Python:   {env_python_version()}")

    print("\nCoMan")
    print(f"> Version:  {__version__}")
    py = sys.version_info
    print(f"> Python:   {py.major}.{py.minor}.{py.micro}")

    print(f"> Root:     {conda_root()}")
    print(f"> Envs dir: {envs_dir()}")
    print(f"> Pkgs dir: {pkgs_dir()}")

    print("\nConda")
    conda_info()


def _env_lock_conda():
    platforms = spec_platforms()
    new_lock_paths = [str(conda_lock_file(p)) for p in platforms]
    for lock_path in glob(str(conda_lock_file("*"))):
        if lock_path not in new_lock_paths:
            print(
                click.style("   lock:", fg="bright_white"),
                "Removing",
                click.style("Conda", fg="green", bold=True),
                "lock file",
                file=sys.stderr,
            )
            os.remove(lock_path)

    for platform in platforms:
        print(
            click.style("   lock:", fg="bright_white"),
            "Generating",
            click.style("Conda", fg="green", bold=True),
            "lock file for",
            click.style(platform, fg="magenta"),
            file=sys.stderr,
        )
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
            print(
                click.style("   lock:", fg="bright_white"),
                "Removing",
                click.style("Pip", fg="cyan", bold=True),
                "lock file",
                file=sys.stderr,
            )
            os.remove(pip_lock_file())
        return

    print(
        click.style("   lock:", fg="bright_white"),
        "Generating",
        click.style("Pip", fg="cyan", bold=True),
        "lock file",
        file=sys.stderr,
    )
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
    prefix = env_prefix()
    lock_path = conda_lock_file()
    print(
        click.style("install:", fg="bright_white"),
        "Installing",
        click.style("Conda", fg="green", bold=True),
        "environment",
        click.style("<create>", fg="red") if prune else click.style("<update>", fg="green"),
        file=sys.stderr,
    )

    args = [
        "create" if prune or not prefix.exists() else "install",
        "--file",
        lock_path,
        "--prefix",
        prefix,
        "--yes",
    ]
    p = run_exe(args, capture=False)
    if p.returncode != 0:
        click.secho(f"\nCould not install {lock_path} into {prefix}", fg="red", file=sys.stderr)
        exit(1)


def _env_install_pip():
    lock_path = pip_lock_file()
    print(
        click.style("install:", fg="bright_white"),
        "Installing",
        click.style("Pip", fg="cyan", bold=True),
        "packages",
        file=sys.stderr,
    )
    args = [
        env_python_exe(),
        "-m",
        "pip",
        "install",
        "-r",
        lock_path,
        "--no-deps",
        "--disable-pip-version-check",
        "--no-input",
    ]
    res = subprocess.run(args)
    if res.returncode != 0:
        print(res.stderr)
        exit(1)


def _pkg_str_lengths(pkg_infos: List[Dict[str, Any]], cols: List[str]):
    return {col: max(map(lambda x: len(x[col]), pkg_infos)) for col in cols}


def _format_pkg_str(pkg_info: dict, cols: List[str], pkg_str_lengths: Dict[str, int], bold: bool = False):
    col_strs = []
    for col in cols:
        if pkg_str_lengths[col] == 0:
            continue
        fg = _COL_COLORS[col]
        if col == "name" and pkg_info["channel"] == "pypi":
            fg = _COL_COLORS["pypi"]
        col_strs.append(click.style(pkg_info.get(col, "").ljust(pkg_str_lengths[col]), fg=fg, bold=bold))

    if pkg_str_lengths.get("old_version", 0) > 0 and pkg_str_lengths.get("version", 0) > 0:
        i = cols.index("old_version")
        oldv = col_strs.pop(i)
        col_strs[i] = f"{oldv} ➜ {col_strs[i]}"
    return "  ".join(col_strs)


def env_install(prune: Optional[bool] = None, force: bool = False, quiet: bool = False, show: bool = False):
    require_spec_file()

    old_pkgs = env_show(deps=True, only_return=True) if show and env_prefix().exists() else []

    use_pip = bool(spec_pip_requirements())
    if not conda_lock_file().exists() or (use_pip and not pip_lock_file().exists()):
        env_lock()

    conda_hash = conda_lock_hash()
    conda_changed = conda_outdated(conda_hash)

    pip_hash = pip_lock_hash()
    pip_changed = pip_outdated(pip_hash)

    if prune is None:
        prune = (use_pip and conda_changed) or pip_changed

    installed = False

    # Conda
    if force or prune or conda_changed:
        _env_install_conda(prune)
        with open(env_prefix() / "conda_hash.txt", "w") as f:
            f.write(conda_hash)
            installed = True
    elif not quiet:
        print(
            click.style("install:", fg="bright_white"),
            click.style("Conda", fg="green", bold=True),
            "environment is already up-to-date",
            file=sys.stderr,
        )

    # Pip
    pip_hash_path = env_prefix() / "pip_hash.txt"
    if pip_hash:
        if force or prune or pip_changed:
            _env_install_pip()
            with open(pip_hash_path, "w") as f:
                f.write(pip_hash)
            installed = True
        elif not quiet:
            print(
                click.style("install:", fg="bright_white"),
                click.style("Pip", fg="cyan", bold=True),
                "packages are already up-to-date",
                file=sys.stderr,
            )
    elif pip_hash_path.exists():
        os.remove(pip_hash_path)

    if not installed:
        return

    print()
    if show:
        new_pkgs = env_show(deps=True, only_return=True)
        if new_pkgs != old_pkgs:
            new_pkg_names = [pkg_info["name"] for pkg_info in new_pkgs]
            old_pkg_names = [pkg_info["name"] for pkg_info in old_pkgs]

            del_pkgs = [pkg for pkg in old_pkgs if pkg["name"] not in new_pkg_names]
            add_pkgs = [pkg for pkg in new_pkgs if pkg["name"] not in old_pkg_names]
            upd_pkgs = [pkg for pkg in new_pkgs if pkg["name"] in old_pkg_names and pkg not in old_pkgs]

            for pkg in del_pkgs:
                pkg["old_version"] = pkg["version"]
                pkg["version"] = ""
            for pkg in add_pkgs:
                pkg["old_version"] = ""
            for pkg in upd_pkgs:
                pkg["old_version"] = old_pkgs[old_pkg_names.index(pkg["name"])]["version"]

            cols = ["name", "old_version", "version", "channel"]
            pkg_str_lengths = _pkg_str_lengths(del_pkgs + add_pkgs + upd_pkgs, cols)

            for pkg_info in del_pkgs:
                line = _format_pkg_str(pkg_info, cols=cols, pkg_str_lengths=pkg_str_lengths)
                print(click.style("install:", fg="bright_white"),
                      click.style("-", fg="red"),
                      "  Removed",
                      line,
                      file=sys.stderr)

            for pkg_info in upd_pkgs:
                line = _format_pkg_str(pkg_info, cols=cols, pkg_str_lengths=pkg_str_lengths)
                print(click.style("install:", fg="bright_white"),
                      click.style("*", fg="yellow"),
                      "  Updated",
                      line,
                      file=sys.stderr)

            for pkg_info in add_pkgs:
                line = _format_pkg_str(pkg_info, cols=cols, pkg_str_lengths=pkg_str_lengths)
                print(click.style("install:", fg="bright_white"),
                      click.style("+", fg="green"),
                      "Installed",
                      line,
                      file=sys.stderr)

            print()


def env_uninstall():
    run_exe(["env", "remove", "--prefix", env_prefix()])


def change_spec(*, add_pkgs: List[str], remove_pkgs: List[str], pip: bool, update: bool, install: Optional[bool],
                prune: Optional[bool], force: bool, show: bool):
    require_spec_file()
    spec_data, save_spec_file = edit_spec_file()

    def _dep_names(deps):
        return [pkg.split(" ")[0] if not isinstance(pkg, OrderedDict) else None for pkg in deps]

    deps = spec_data["dependencies"]
    dep_names = _dep_names(deps)

    def _add_pkg(pkg: str, pip: bool):
        if pip:
            pkg_info = pypi_pkg_info(pkg)
        else:
            pkg_info = conda_pkg_info(pkg, spec_data["channels"])

        name = pkg_info["name"]
        pkg = f"{name} >={pkg_info['version']}"
        channel = pkg_info["channel"]

        i = len(deps)
        if name in dep_names:
            i = dep_names.index(name)
            if deps[i] == pkg:
                return False
            deps.pop(i)

        deps.insert(i, pkg)
        dep_names.insert(i, name)

        name, ver = pkg.split()
        pkg_str = f"{click.style(name, fg='cyan' if pip else 'green')} ({click.style(ver, fg='blue')})"
        print(click.style("   spec:", fg="bright_white"), f"Added {pkg_str} to dependencies", file=sys.stderr)
        return True

    def _remove_pkg(pkg: str):
        if pkg not in dep_names:
            return False

        i = dep_names.index(pkg)
        pkg = deps.pop(i)
        dep_names.pop(i)

        name, ver = pkg.split()
        pkg_str = f"{click.style(name, fg='cyan' if pip else 'green')} ({click.style(ver, fg='blue')})"
        print(click.style("   spec:", fg="bright_white"), f"Removed {pkg_str} from dependencies", file=sys.stderr)
        return True

    lock_conda = not pip
    if pip:
        if "pip" not in dep_names:
            lock_conda = _add_pkg("pip", pip=False)
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

    if not changed:
        if add_pkgs:
            print(click.style("   spec:", fg="bright_white"),
                  click.style("No new dependencies added", fg="yellow"),
                  file=sys.stderr)
        if remove_pkgs:
            print(click.style("   spec:", fg="bright_white"),
                  click.style("No dependencies removed", fg="yellow"),
                  file=sys.stderr)

    save_spec_file()
    if update:
        print(file=sys.stderr)
        env_lock(conda=lock_conda)

    if install is None:
        install = env_prefix().exists()

    prune = len(remove_pkgs) > 0 or prune
    if update and install:
        print(file=sys.stderr)
        env_install(prune=prune, force=force, show=show)


def env_show(query: List[str] = [], deps: bool = False, pip: Optional[bool] = None, only_return: bool = False):
    p = run_exe(["list", "--prefix", env_prefix(), *query, "--json"])
    if not p.stdout:
        print("No results", file=sys.stderr)
        exit(1)

    conda_names, pip_names = spec_package_names()

    pkg_infos = json.loads(p.stdout)
    if pip is False:
        pkg_infos = [x for x in pkg_infos if x["channel"] != "pypi"]
    if pip is True:
        pkg_infos = [x for x in pkg_infos if x["channel"] == "pypi"]
    elif not deps:
        pkg_infos = [x for x in pkg_infos if x["name"] in conda_names or x["name"] in pip_names]

    if only_return:
        return pkg_infos

    cols = ["name", "version", "channel"]
    pkg_str_lengths = _pkg_str_lengths(pkg_infos, cols) if not only_return else {}

    for pkg_info in pkg_infos:
        name = pkg_info["name"]

        warning = None
        if pkg_info["channel"] == "pypi":
            if name in conda_names:
                warning = "WARNING: conda dependency overriden by pip"
            elif name not in pip_names:
                warning = "WARNING: implicit pip dependency"
        line = _format_pkg_str(pkg_info, cols=cols, pkg_str_lengths=pkg_str_lengths)
        if warning:
            line = f"{line}  {click.style(warning, fg='yellow')}"
        print(line)

    return pkg_infos


def conda_search(pkg: str,
                 channels: Optional[List[str]] = None,
                 platform: Optional[str] = None) -> List[Dict[str, Any]]:
    channels = channels or spec_channels()
    args = []
    for c in channels:
        args.extend(["-c", c])
    if platform:
        args.extend(["--subdir", platform])

    p = run_exe(["search", pkg, *args, "--json"], check=False, exe=conda_exe())
    if not p.stdout:
        print(f"Unable to query package through '{system_exe()}'", file=sys.stderr)
        exit(1)
    res = json.loads(p.stdout)
    if "error" in res:
        print(res["error"], file=sys.stderr)
        exit(1)

    if pkg not in res:
        print(f"Package '{pkg}' not found. Did you mean: {', '.join(sorted(res))}", file=sys.stderr)
        exit(1)

    info = res[pkg]
    for pkg_info in info:
        pkg_info["channel"] = pkg_info["channel"].split("/")[-2]
    return info


def conda_pkg_info(pkg: str, channels: Optional[List[str]] = None):
    return conda_search(pkg, channels)[-1]


def env_search(pkg: str, platform: Optional[str], limit: int):
    platforms = [platform] if platform else spec_platforms()

    cols = ["name", "version", "build", "channel"]

    for i, platform in enumerate(platforms, start=1):
        if i > 1:
            print()
        pkg_infos = conda_search(pkg, platform=platform)
        if limit > 0:
            pkg_infos = pkg_infos[-limit:]
        click.secho(f"# platform: {click.style(platform, bold=True)}", fg="magenta")
        if not pkg_infos:
            click.secho("No results", fg="yellow")
            exit(1)

        pkg_str_lengths = _pkg_str_lengths(pkg_infos, cols)
        for j, pkg_info in enumerate(pkg_infos, start=1):
            print(_format_pkg_str(pkg_info, cols=cols, pkg_str_lengths=pkg_str_lengths, bold=j == len(pkg_infos)))


def env_init():
    if spec_file().exists():
        print(f"Specification file `{spec_file()}` already exists", file=sys.stderr)
        exit(1)

    print(
        click.style("   init:", fg="bright_white"),
        "Creating",
        click.style("environment.yml", fg="green"),
        file=sys.stderr,
    )
    pkg_info = conda_pkg_info("python", ["conda-forge"])
    pkg_str = f"{pkg_info['name']} >={pkg_info['version']}"

    with open(spec_file(), "w") as f:
        f.write(f"channels:\n- conda-forge\n\nplatforms:\n- {system_platform()}\n\ndependencies:\n- {pkg_str}\n")

    print()
    env_lock()
