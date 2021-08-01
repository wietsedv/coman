import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from glob import glob
from json.decoder import JSONDecodeError
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

import click
from semantic_version import SimpleSpec, Version

from coman._version import __version__
from coman.spec import (Specification, conda_lock_file, conda_lock_hash, pip_lock_comments, pip_lock_file,
                        pip_lock_hash, spec_pip_requirements)
from coman.system import Conda, conda_exe, conda_info, conda_pkg_info, conda_search, micromamba_exe
from coman.utils import COLORS, format_pkg_line, pkg_col_lengths


class LockSpecification:
    def __init__(self, specs: List[str], channels: List[str], platform: str):
        self.specs = specs
        self.channels = channels
        self.platform = platform

    def env_hash(self) -> str:
        env_spec = json.dumps(
            {
                "channels": self.channels,
                "platform": self.platform,
                "specs": sorted(self.specs),
            },
            sort_keys=True,
        )
        return hashlib.sha256(env_spec.encode("utf-8")).hexdigest()


def conda_env_override(conda: Conda, platform) -> Dict[str, str]:
    env = dict(os.environ)
    env.update({
        "CONDA_SUBDIR": platform,
        "CONDA_PKGS_DIRS": str(conda.pkgs_dir),
        "CONDA_UNSATISFIABLE_HINTS_CHECK_DEPTH": "0",
        "CONDA_ADD_PIP_AS_PYTHON_DEPENDENCY": "False",
    })
    return env


def search_for_md5s(conda: Conda, package_specs: List[dict], platform: str, channels: List[str]):
    """Use conda-search to determine the md5 metadata that we need.

    This is only needed if pkgs_dirs is set in condarc.
    Sadly this is going to be slow since we need to fetch each result individually
    due to the cli of conda search

    """
    def matchspec(spec):
        return (f"{spec['name']}["
                f"version={spec['version']},"
                f"subdir={spec['platform']},"
                f"channel={spec['channel']},"
                f"build={spec['build_string']}"
                "]")

    found: Set[str] = set()
    packages: List[Tuple[str, str]] = [
        *[(d["name"], matchspec(d)) for d in package_specs],
        *[(d["name"], f"{d['name']}[url='{d['url_conda']}']") for d in package_specs],
        *[(d["name"], f"{d['name']}[url='{d['url']}']") for d in package_specs],
    ]

    for name, spec in packages:
        if name in found:
            continue
        channel_args = []
        for c in channels:
            channel_args += ["-c", c]
        cmd = [str(conda.exe), "search", *channel_args, "--json", spec]
        out = subprocess.run(
            cmd,
            encoding="utf8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=conda_env_override(conda, platform),
        )
        content = json.loads(out.stdout)
        if name in content:
            assert len(content[name]) == 1
            yield content[name][0]
            found.add(name)


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


def parse_environment_file(spec_file: Path, platform: str):
    import ruamel.yaml
    with spec_file.open("r") as f:
        filtered_content = "\n".join(filter_platform_selectors(f.read(), platform=platform))
        env_yaml_data = ruamel.yaml.safe_load(filtered_content)

    specs = [x for x in env_yaml_data["dependencies"] if isinstance(x, str)]
    channels = env_yaml_data.get("channels", [])

    return LockSpecification(specs=specs, channels=channels, platform=platform)


def parse_unsatisfiable_error(msg: str):
    spec_re = re.compile(
        r"(- )?(?:([a-zA-Z0-9_-]+)(?:\[version='([^']+)'\]|(==?[^ ]+)) -> )?(\w+)(?:\[version='([^']+)'\]|(==?[^ ]+))?")

    conflicts = {}
    incompatible = set()
    for line in msg.splitlines():
        m = spec_re.match(line.strip().replace("The following", " The following"))
        if m:
            compat, spec_name, spec_ver, spec_ver_, dep_name, dep_ver, dep_ver_ = m.groups()
            spec_ver = spec_ver or spec_ver_
            dep_ver = dep_ver or dep_ver_
            if spec_ver is None and dep_ver is None:
                continue

            if dep_name not in conflicts:
                conflicts[dep_name] = {"spec": None, "children": [], "compatible": True}

            if spec_name is None:
                conflicts[dep_name]["spec"] = dep_ver
            else:
                if compat is not None:
                    incompatible.add(spec_name)
                    conflicts[dep_name]["compatible"] = False
                conflicts[dep_name]["children"].append((spec_name, spec_ver, dep_name, dep_ver))
    return conflicts


def solve_specs_for_arch(conda: Conda, channels: List[str], specs: List[str], platform: str) -> dict:
    args = [
        str(conda.exe),
        "create",
        "--prefix",
        os.path.join(conda.pkgs_dir, "prefix"),
        "--dry-run",
        "--json",
    ]
    conda_flags = os.environ.get("CONDA_FLAGS")
    if conda_flags:
        args.extend(shlex.split(conda_flags))
    if channels:
        args.append("--override-channels")
    for channel in channels:
        args.extend(["--channel", channel])
    args.extend(specs)

    p = subprocess.run(
        args,
        env=conda_env_override(conda, platform),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf8",
    )

    try:
        res = json.loads(p.stdout)
    except JSONDecodeError:
        click.secho("\nFailed to lock the environment\n", fg="red", file=sys.stderr)
        print(p.stdout.strip())
        exit(1)

    if p.returncode != 0:
        exception_name = res.get("exception_name", None)

        line = click.style("\nFailed to lock the environment", fg="red")
        if exception_name:
            line += " " + click.style(f"[{exception_name}]", fg="bright_white")
        print(line + "\n", file=sys.stderr)

        if exception_name == "PackagesNotFoundError":
            pkg_names = res["packages"]
            print(f"The following packages are not available from current channels:\n", file=sys.stderr)
            for pkg_name in pkg_names:
                print(f"- {pkg_name}", file=sys.stderr)
            sys.exit(1)

        if exception_name == "UnsatisfiableError":
            res_ = parse_unsatisfiable_error(res["message"])
            if res_:
                incompatible = set()
                for pkg_name, info in res_.items():
                    if not info["compatible"]:
                        incompatible.add(pkg_name)
                    print(
                        "Cannot determine version of" if info["compatible"] else "Cannot find package",
                        click.style(pkg_name, fg=COLORS["name"] if info["compatible"] else "red"),
                        *([click.style(info['spec'], fg=COLORS['version'])] if info["spec"] else []),
                        file=sys.stderr,
                    )
                    for child_name, child_ver, dep_name, dep_ver in info["children"]:
                        print(
                            f"-",
                            click.style(child_name, fg=COLORS["name"]),
                            click.style(child_ver, fg=COLORS['version']),
                            "requires",
                            click.style(dep_name, fg=COLORS["name"] if info["compatible"] else "red"),
                            click.style(dep_ver or "", fg=COLORS['version']),
                            file=sys.stderr,
                        )
                    print(file=sys.stderr)

                incompatible = sorted(incompatible)
                if len(incompatible) == 1:
                    pkg_name = incompatible[0]
                    print(
                        f"The root cause may be that {click.style(pkg_name, fg='red')} is unavailable on this platform",
                        file=sys.stderr)
                elif len(incompatible) > 1:
                    pkg_names = ", ".join([click.style(pkg_name, fg='red') for pkg_name in incompatible])
                    print(f"The root cause may be that these packages are unavailble on this platform:",
                          pkg_names,
                          file=sys.stderr)
                sys.exit(1)

        if "message" in res:
            print(res["message"], file=sys.stderr)
            sys.exit(1)

        print(json.dumps(res, indent=2), file=sys.stderr)
        sys.exit(1)

    return res


def fn_to_dist_name(fn: str) -> str:
    if fn.endswith(".conda"):
        fn, _, _ = fn.partition(".conda")
    elif fn.endswith(".tar.bz2"):
        fn, _, _ = fn.partition(".tar.bz2")
    else:
        raise RuntimeError(f"unexpected file type {fn}", fn)
    return fn


def create_lockfile_from_spec(conda: Conda, spec: LockSpecification) -> List[str]:
    dry_run_install = solve_specs_for_arch(
        conda,
        platform=spec.platform,
        channels=spec.channels,
        specs=spec.specs,
    )

    lockfile_contents = [
        "# Generated by conda-lock.",
        f"# platform: {spec.platform}",
        f"# env_hash: {spec.env_hash()}\n",
        "@EXPLICIT\n",
    ]

    link_actions = dry_run_install["actions"]["LINK"]
    for link in link_actions:
        if conda.is_micromamba():
            link["url_base"] = fn_to_dist_name(link["url"])
            link["dist_name"] = fn_to_dist_name(link["fn"])
        else:
            link["url_base"] = f"{link['base_url']}/{link['platform']}/{link['dist_name']}"

        link["url"] = f"{link['url_base']}.tar.bz2"
        link["url_conda"] = f"{link['url_base']}.conda"
    link_dists = {link["dist_name"] for link in link_actions}

    fetch_actions = dry_run_install["actions"]["FETCH"]

    fetch_by_dist_name = {fn_to_dist_name(pkg["fn"]): pkg for pkg in fetch_actions}

    non_fetch_packages = link_dists - set(fetch_by_dist_name)
    if len(non_fetch_packages) > 0:
        for search_res in search_for_md5s(
                conda=conda,
                package_specs=[x for x in link_actions if x["dist_name"] in non_fetch_packages],
                platform=spec.platform,
                channels=spec.channels,
        ):
            dist_name = fn_to_dist_name(search_res["fn"])
            fetch_by_dist_name[dist_name] = search_res

    for pkg in link_actions:
        dist_name = (fn_to_dist_name(pkg["fn"]) if conda.is_micromamba() else pkg["dist_name"])
        url = fetch_by_dist_name[dist_name]["url"]
        md5 = fetch_by_dist_name[dist_name]["md5"]
        lockfile_contents.append(f"{url}#{md5}")

    def sanitize_lockfile_line(line):
        line = line.strip()
        if line == "":
            return "#"
        else:
            return line

    lockfile_contents = [sanitize_lockfile_line(line) for line in lockfile_contents]

    return lockfile_contents


def conda_outdated(conda: Conda, conda_hash: Optional[str] = None) -> bool:
    conda_hash = conda_hash or conda_lock_hash(conda.env.platform)
    return conda.env.conda_hash != conda_hash


def pip_outdated(conda: Conda, pip_hash: Optional[str] = None) -> bool:
    pip_hash = pip_hash or pip_lock_hash()
    return conda.env.pip_hash != pip_hash


def env_info(conda: Conda, spec: Specification):
    print("Current environment")
    sys_status = "up-to-date"
    if not spec.spec_file.exists():
        sys_status = "no environment.yml (run `coman init`)"
    elif not conda_lock_file(conda.env.platform).exists():
        sys_status = f"no lock file for this platform (run `coman lock`)"
    elif not conda.env.prefix.exists():
        sys_status = "not installed (run `coman install`)"
    elif conda_outdated(conda):
        sys_status = "env outdated (run `coman install`)"
    elif pip_outdated(conda):
        sys_status = "pip outdated (run `coman install`)"

    print(f"> Prefix:   {conda.env.prefix}")
    print(f"> Platform: {conda.env.platform}")
    print(f"> Status:   {sys_status}")
    if conda.env.prefix.exists():
        print(f"> Python:   {conda.env.python_version}")

    print("\nCoMan")
    print(f"> Version:  {__version__}")
    py = sys.version_info
    print(f"> Python:   {py.major}.{py.minor}.{py.micro}")

    print(f"> Root:     {conda.root}")
    print(f"> Envs dir: {conda.envs_dir}")
    print(f"> Pkgs dir: {conda.pkgs_dir}")

    print("\nConda")
    conda_info()


def _env_lock_conda(conda: Conda, spec: Specification):
    platforms = spec.platforms(conda.env.platform)
    if conda.env.platform not in platforms:
        click.secho(f"WARNING: Platform {conda.env.platform} is not whitelisted in {spec.spec_file}\n",
                    fg="yellow",
                    file=sys.stderr)

    new_lock_paths = [str(conda_lock_file(p)) for p in platforms]
    for lock_path in glob(str(conda_lock_file("*"))):
        if lock_path not in new_lock_paths:
            print(
                click.style("   lock:", fg="bright_white"),
                "Removing",
                click.style("Conda", fg="green", bold=True),
                "lock file",
                click.style(f"[{lock_path}]", fg="bright_white"),
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
        lock_spec = parse_environment_file(spec.spec_file, platform)
        lock_contents = create_lockfile_from_spec(conda, lock_spec)
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


def _env_lock_pip(spec: Specification):
    requirements = spec_pip_requirements(spec)
    if not requirements:
        lock_path = pip_lock_file()
        if lock_path.exists():
            print(
                click.style("   lock:", fg="bright_white"),
                "Removing",
                click.style("Pip", fg="cyan", bold=True),
                "lock file",
                click.style(f"[{lock_path}]", fg="bright_white"),
                file=sys.stderr,
            )
            os.remove(lock_path)
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


def env_lock(conda: Conda, spec: Specification, lock_conda: bool = True, lock_pip: bool = True):
    if lock_conda:
        _env_lock_conda(conda, spec)
    if lock_pip:
        _env_lock_pip(spec)


def _env_install_conda(conda: Conda, prune: bool):
    lock_path = conda_lock_file(conda.env.platform)
    print(
        click.style("install:", fg="bright_white"),
        "Installing",
        click.style("Conda", fg="green", bold=True),
        "environment",
        click.style("<create>", fg="red") if prune else click.style("<update>", fg="green"),
        file=sys.stderr,
    )

    args = [
        "create" if prune or not conda.env.prefix.exists() else "install",
        "--file",
        lock_path,
        "--prefix",
        conda.env.prefix,
        "--yes",
    ]
    p = conda.run(args, capture=False)
    if p.returncode != 0:
        click.secho(f"\nCould not install {lock_path} into {conda.env.prefix}", fg="red", file=sys.stderr)
        exit(1)


def _env_install_pip(python: Path):
    lock_path = pip_lock_file()
    print(
        click.style("install:", fg="bright_white"),
        "Installing",
        click.style("Pip", fg="cyan", bold=True),
        "packages",
        file=sys.stderr,
    )
    args = [
        python,
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


def env_install(conda: Conda,
                spec: Specification,
                prune: Optional[bool] = None,
                force: bool = False,
                quiet: bool = False,
                show: bool = False):
    if conda.env.platform not in spec.platforms(conda.env.platform):
        click.secho(f"Cannot install because {conda.env.platform} is not whitelisted in {spec.spec_file}",
                    fg="red",
                    file=sys.stderr)
        click.secho(f"You can add it with: `coman platform add {conda.env.platform}`", fg="red", file=sys.stderr)
        exit(1)

    old_pkgs = env_show(conda, spec, deps=True, only_return=True) if show and conda.env.prefix.exists() else []

    use_pip = bool(spec_pip_requirements(spec))
    if not conda_lock_file(conda.env.platform).exists() or (use_pip and not pip_lock_file().exists()):
        env_lock(conda, spec)

    conda_hash = conda_lock_hash(conda.env.platform)
    conda_changed = conda_outdated(conda, conda_hash)

    pip_hash = pip_lock_hash()
    pip_changed = pip_outdated(conda, pip_hash)

    if prune is None:
        prune = (use_pip and conda_changed) or pip_changed

    installed = False

    # Conda
    if force or prune or conda_changed:
        _env_install_conda(conda, prune)
        with open(conda.env.prefix / "conda_hash.txt", "w") as f:
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
    pip_hash_path = conda.env.prefix / "pip_hash.txt"
    if pip_hash:
        if force or prune or pip_changed:
            _env_install_pip(conda.env.python)
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

    print(file=sys.stderr)
    if show:
        new_pkgs = env_show(conda, spec, deps=True, only_return=True)
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

            col_lengths = pkg_col_lengths(del_pkgs + add_pkgs + upd_pkgs, ["name", "old_version", "version", "channel"])

            for pkg_info in del_pkgs:
                line = format_pkg_line(pkg_info, col_lengths)
                print(click.style("install:", fg="bright_white"),
                      click.style("-", fg="red"),
                      "  Removed",
                      line,
                      file=sys.stderr)

            for pkg_info in upd_pkgs:
                line = format_pkg_line(pkg_info, col_lengths)
                print(click.style("install:", fg="bright_white"),
                      click.style("*", fg="yellow"),
                      "  Updated",
                      line,
                      file=sys.stderr)

            for pkg_info in add_pkgs:
                line = format_pkg_line(pkg_info, col_lengths)
                print(click.style("install:", fg="bright_white"),
                      click.style("+", fg="green"),
                      "Installed",
                      line,
                      file=sys.stderr)

            print(file=sys.stderr)


def env_uninstall(conda: Conda):
    conda.run(["env", "remove", "--prefix", conda.env.prefix])


def env_show(conda: Conda,
             spec: Specification,
             query: List[str] = [],
             deps: bool = False,
             pip: Optional[bool] = None,
             only_return: bool = False):
    p = conda.run(["list", "--prefix", conda.env.prefix, *query, "--json"])
    if not p.stdout:
        print("No results", file=sys.stderr)
        exit(1)

    conda_names, pip_names = spec.dependencies()

    pkg_infos = json.loads(p.stdout)
    if pip is False:
        pkg_infos = [x for x in pkg_infos if x["channel"] != "pypi"]
    if pip is True:
        pkg_infos = [x for x in pkg_infos if x["channel"] == "pypi"]
        pip_comments = pip_lock_comments()
        for pkg_info in pkg_infos:
            pkg_info["comment"] = pip_comments.get(pkg_info["name"], "")
    elif not deps:
        pkg_infos = [x for x in pkg_infos if x["name"] in conda_names or x["name"] in pip_names]

    if only_return:
        return pkg_infos

    col_lengths = pkg_col_lengths(pkg_infos, ["name", "version", "channel", "comment"]) if not only_return else {}

    for pkg_info in pkg_infos:
        name = pkg_info["name"]

        warning = None
        if pkg_info["channel"] == "pypi":
            if name in conda_names:
                warning = "WARNING: conda dependency overriden by pip"
            # elif name not in pip_names:
            #     warning = "WARNING: implicit pip dependency"
        line = format_pkg_line(pkg_info, col_lengths)
        if warning:
            line = f"- {line}  {click.style(warning, fg='yellow')}"
        print(line)

    return pkg_infos


def env_search(conda: Conda, spec: Specification, pkg: str, platform: Optional[str], limit: int, deps: bool):
    platforms = [platform] if platform else spec.platforms(conda.env.platform)
    channels = spec.channels()

    python_ver = conda.env.python_version
    if python_ver:
        print("Python:  ", python_ver)

    print("Channels:", ", ".join([click.style(c, fg=COLORS["channel"]) for c in channels]) + "\n", file=sys.stderr)

    for i, platform in enumerate(platforms, start=1):
        if i > 1:
            print(file=sys.stderr)
        pkg_infos = conda_search(conda, pkg, channels=channels, platform=platform)
        click.secho(f"# platform: {click.style(platform, bold=True)}", fg="magenta")
        if not pkg_infos:
            click.secho("No results", fg="yellow")
            exit(1)

        # Filter Python version
        if python_ver:
            suffix_re = re.compile("(-?[a-z]+[0-9]*)|(\*)$")
            py_pkg_infos = []
            for pkg_info in pkg_infos:
                ok = True
                for dep in pkg_info["depends"]:
                    name, *args = dep.split()
                    if name == "python":
                        if len(args) > 0:
                            ver = suffix_re.sub("", args[0])
                            ok = SimpleSpec(ver).match(python_ver)
                        break
                if ok:
                    py_pkg_infos.append(pkg_info)
            pkg_infos = py_pkg_infos

        if limit > 0:
            pkg_infos = pkg_infos[-limit:]

        cols = ["name", "version", "build", "channel", "platform"]
        if deps:
            for pkg_info in pkg_infos:
                pkg_info["depends"] = pkg_info["depends"] = "\n" + "".join(
                    [f"- {dep}\n" for dep in pkg_info["depends"]])
            cols.append("depends")

        col_lengths = pkg_col_lengths(pkg_infos, cols)
        for j, pkg_info in enumerate(pkg_infos, start=1):
            print(format_pkg_line(pkg_info, col_lengths, bold=j == len(pkg_infos)))


def env_init(conda: Conda, spec: Specification, force: bool):
    if not force and spec.spec_file.exists():
        print(f"Specification file `{spec.spec_file}` already exists", file=sys.stderr)
        exit(1)

    print(
        click.style("   init:", fg="bright_white"),
        "Creating",
        click.style("environment.yml", fg="green"),
        file=sys.stderr,
    )
    pkg_info = conda_pkg_info(conda, "python", channels=["conda-forge"])

    v = Version(pkg_info['version'])
    pkg_str = f"{pkg_info['name']} >={v},<={v.next_minor()}"

    with open(spec.spec_file, "w") as f:
        f.write(f"platforms:\n- {conda.env.platform}\nchannels:\n- conda-forge\ndependencies:\n- {pkg_str}\n")
    spec.data = None

    print(file=sys.stderr)
    env_lock(conda, spec)


def env_shell_hook(conda: Conda, quiet: bool, shell_type: str):
    # Currently only works with conda or micromamba
    if not conda.is_micromamba():
        exe = conda.exe if conda.is_conda(standalone=False) else conda_exe()
        if exe:
            if not quiet:
                print("You can deactivate the environment with `conda deactivate`", file=sys.stderr)
            print(f"eval \"$('{exe}' shell.{shell_type} hook)\" && conda activate \"{conda.env.prefix}\"")
            exit(0)

    if not quiet:
        print("You can deactivate the environment with `micromamba deactivate`", file=sys.stderr)
    exe = conda.exe if conda.is_micromamba() else micromamba_exe()
    print(f"eval \"$('{exe}' shell hook -s {shell_type})\" && micromamba activate \"{conda.env.prefix}\"")
