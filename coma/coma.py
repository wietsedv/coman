import json
import os
import re
import subprocess
import sys
from distutils.version import LooseVersion
from glob import glob
from hashlib import md5
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Tuple

from ensureconda import ensureconda
from ensureconda.api import (determine_conda_version, determine_mamba_version, determine_micromamba_version)
from ensureconda.installer import install_conda_exe, install_micromamba
from ensureconda.resolve import (conda_executables, conda_standalone_executables, mamba_executables,
                                 micromamba_executables, platform_subdir)

import click
import ruamel.yaml as yaml
from conda_lock.conda_lock import run_lock

from ._version import __version__

ENV_HASH_PATTERN = re.compile(r"^# env_hash: (.*)$")
PLATFORM_PATTERN = re.compile(r"^# platform: (.*)$")

MIN_CONDA_VERSION = LooseVersion("4.9")
MIN_MAMBA_VERSION = LooseVersion("0.15")


def env_file():
    return Path("environment.yml")


def lock_file(platform: str):
    return Path(f"conda-{platform}.lock")


def safe_next(it: Iterator[os.PathLike]):
    try:
        return next(it)
    except StopIteration:
        return None


def is_mamba(exe: Path) -> bool:
    return str(exe).endswith("/mamba")


def is_conda(exe: Path) -> bool:
    return str(exe).endswith("/conda")


def current_exe():
    exe = ensureconda(
        mamba=True,
        micromamba=False,
        conda=True,
        conda_exe=True,
        min_mamba_version=MIN_MAMBA_VERSION,
        min_conda_version=MIN_CONDA_VERSION,
    )
    if not exe:
        raise RuntimeError("No valid conda installation was found")
    return Path(exe)


def current_envs_dir(exe: Path):
    res = json.loads(subprocess.check_output([exe, "info", "--json"]))
    return Path(res["envs_dirs"][0])


def current_name():
    current_dir = os.path.normpath(os.getcwd())
    current_basename = os.path.basename(current_dir)
    hash = md5(current_dir.encode("utf-8")).hexdigest()[:8]
    return f"{current_basename}-{hash}"


def current_prefix(exe: Path):
    envs_dir = current_envs_dir(exe)
    return envs_dir / current_name()


def current_env_hash(prefix: Path) -> Optional[str]:
    env_hash_file = prefix / "env_hash.txt"
    if env_hash_file.exists():
        with open(env_hash_file) as f:
            return f.read().strip()


def save_env_hash(prefix: Path, env_hash: str):
    with open(prefix / "env_hash.txt", "w") as f:
        f.write(env_hash)


def current_platforms() -> List[str]:
    if not env_file().exists():
        return []

    with open(env_file()) as f:
        env = yaml.safe_load(f)
    return env.get("platforms", [platform_subdir()])


def current_env_spec() -> Tuple[dict, Callable]:
    environment_file = env_file()
    if not env_file().exists():
        raise FileNotFoundError(f"{environment_file} not found")

    yaml_ = yaml.YAML()
    with open(environment_file) as f:
        env = yaml_.load(f)

    if "channels" not in env:
        env["channels"] = ["conda-forge"]
    if "dependencies" not in env:
        env["dependencies"] = ["python"]

    def save_func():
        with open(environment_file, "w") as f:
            yaml_.dump(env, f)
        _lock()

    return env, save_func


def repoquery_search(exe: Path, spec: str, channels: List[str]):
    args = []
    for c in channels:
        args.extend(["-c", c])
    res = json.loads(subprocess.check_output([exe, "repoquery", "search", spec, *args, "--json"]))["result"]
    if res["msg"]:
        print(res["msg"])
        exit(1)
    pkg = max(res["pkgs"], key=lambda pkg: pkg["timestamp"])
    return pkg


def extract_platform(lock_str: str) -> str:
    for line in lock_str.strip().split("\n"):
        m = PLATFORM_PATTERN.search(line)
        if m:
            return m.group(1)
    raise RuntimeError("Cannot find platform in lockfile")


def extract_env_hash(lock_str: str) -> str:
    for line in lock_str.strip().split("\n"):
        m = ENV_HASH_PATTERN.search(line)
        if m:
            return m.group(1)
    raise RuntimeError("Cannot find env_hash in lockfile")


class NaturalOrderGroup(click.Group):
    def list_commands(self, _):
        return self.commands.keys()


@click.group(cls=NaturalOrderGroup)
@click.version_option()
def cli():
    pass


@cli.command()
@click.option("--name", default=False, is_flag=True)
@click.option("--prefix", default=False, is_flag=True)
@click.option("--platform", default=False, is_flag=True)
def info(name, prefix, platform):
    """
    Info about environment and current system
    """
    if name:
        return print(current_name())
    if prefix:
        exe = current_exe()
        return print(current_prefix(exe))
    if platform:
        return print(platform_subdir())

    exe = current_exe()
    prefix = current_prefix(exe)
    platform = platform_subdir()

    print("Current environment")
    env_status = "up-to-date"
    if not env_file().exists():
        env_status = "no environment.yml (run `coma init`)"
    elif not lock_file(platform).exists():
        env_status = f"no lock file for this platform (run `coma lock`)"
    elif not prefix.exists():
        env_status = "not installed (run `coma install`)"
    else:
        with open(lock_file(platform)) as f:
            lock_str = f.read()
        if extract_env_hash(lock_str) != current_env_hash(prefix):
            env_status = "outdated (run `coma install`)"

    print(f"> Path:     {prefix}")
    print(f"> Platform: {platform}")
    print(f"> Status:   {env_status}")

    print("\nCoMa")
    print(f"> Version:  {__version__}")
    py = sys.version_info
    print(f"> Python:   {py.major}.{py.minor}.{py.micro}")
    print(f"> Envs dir: {current_envs_dir(exe)}")

    print("\nConda")

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


@cli.command()
def init():
    """
    Initialize a new environment.yml
    """
    if env_file().exists():
        print("environment.yml already exists.")
        exit(1)

    exe = current_exe()

    pkg = repoquery_search(exe, "python", ["conda-forge"])
    spec = f"{pkg['name']} >={pkg['version']}"

    with open(env_file(), "w") as f:
        f.write(f"channels:\n- conda-forge\n\nplatforms:\n- {platform_subdir()}\n\ndependencies:\n- {spec}\n")

    _lock()
    print(f"initialized environment.yml and conda-{platform_subdir()}.lock")


def _lock():
    platforms = current_platforms()

    lock_files = [str(lock_file(p)) for p in platforms]
    for filename in glob(str(lock_file("*"))):
        if filename not in lock_files:
            os.remove(filename)

    run_lock(
        environment_files=[env_file()],
        conda_exe=None,
        platforms=platforms,
        mamba=True,
        micromamba=False,
        include_dev_dependencies=True,
        channel_overrides=None,
        kinds=["explicit"],
        filename_template=str(lock_file("{platform}")),
    )


@cli.command()
def lock():
    """
    Lock the package specifications
    """
    _lock()


def _install(prune: bool, lazy: bool = False, **kwargs):
    exe = kwargs.get("exe", current_exe())
    prefix = kwargs.get("prefix", current_prefix(exe))

    lock_filename = lock_file(platform_subdir())
    if not lock_filename.exists():
        _lock()
    with open(lock_filename) as f:
        lock_str = f.read()

    if extract_platform(lock_str) != platform_subdir():
        raise RuntimeError("Platform of lock file does not match current platform")
    env_hash = extract_env_hash(lock_str)
    if lazy and env_hash == current_env_hash(prefix):
        return

    args = [
        str(exe),
        "create" if prune or not prefix.exists() else "update",
        "--file",
        str(lock_filename),
        "--prefix",
        str(prefix),
        "--yes",
    ]
    p = subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        print(p.stdout)
        print(p.stderr)
        print(f"Could not install {lock_filename} into {prefix}")
        exit(1)

    save_env_hash(prefix, env_hash)


@cli.command()
@click.option("--prune", default=False, is_flag=True)
def install(prune: bool):
    """
    Install the environment based on the lock file
    """
    _install(prune)


@cli.command()
def uninstall():
    """
    Uninstall the environment

    You must deactivate the environment before you can remove it.
    """
    exe = current_exe()
    prefix = current_prefix(exe)
    subprocess.run([exe, "env", "remove", "--prefix", prefix])


@cli.command()
@click.option("--prune", default=False, is_flag=True)
def update(prune: bool):
    """
    Update the lock file(s) and install the new environment
    """
    _lock()
    _install(prune)


def change_specs(add_specs: List[str] = [], remove_specs: List[str] = []):
    env, save_func = current_env_spec()
    exe = current_exe()
    prefix = current_prefix(exe)

    dep_names = [spec.split(" ")[0] for spec in env["dependencies"]]
    changed = False

    # Add
    for spec in add_specs:
        pkg = repoquery_search(exe, spec, env["channels"])
        name = pkg['name']
        spec = f"{name} >={pkg['version']}"

        i = len(env["dependencies"])
        if name in dep_names:
            i = dep_names.index(name)
            if env["dependencies"][i] == spec:
                continue
            env["dependencies"].pop(i)

        env["dependencies"].insert(i, spec)
        dep_names.insert(i, name)
        changed = True

    # Remove
    for spec in remove_specs:
        if spec not in dep_names:
            print(f"Dependency {spec} not found")
            continue
        i = dep_names.index(spec)
        env["dependencies"].pop(i)
        dep_names.pop(i)
        changed = True

    # Update
    if changed:
        save_func()
        if prefix.exists():
            _install(prune=False, lazy=True, exe=exe, prefix=prefix)


@cli.command()
@click.argument("specs", nargs=-1)
def add(specs: List[str]):
    """
    Add a package to environment.yml, update the lock file(s) and install the environment
    """
    change_specs(add_specs=specs)


@cli.command()
@click.argument("specs", nargs=-1)
def remove(specs: List[str]):
    """
    Remove a package from environment.yml, update the lock file(s) and install the environment
    """
    change_specs(remove_specs=specs)


@cli.command()
@click.argument("query", nargs=-1)
def show(query: List[str]):
    """
    Show packages in the current environment
    """
    exe = current_exe()
    subprocess.run([exe, "list", "--prefix", current_prefix(exe), *query, "--quiet"])


@cli.command()
@click.argument("args", nargs=-1)
def run(args):
    """
    Run a command within the environment

    Automatically installs the environment if it does not exist yet.
    """
    exe = current_exe()
    prefix = current_prefix(exe)
    _install(prune=False, lazy=True, exe=exe, prefix=prefix)

    # Currently only works with conda
    if not is_conda(exe):
        exe = safe_next(conda_executables())
    if not exe:
        print("CoMa run only works if regular conda is available on your system.")
        exit(1)

    subprocess.run([exe, "run", "--prefix", prefix, "--no-capture-out", "--live-stream", *args])


@cli.command()
@click.option("--force-micromamba", default=False, is_flag=True)
def shell(force_micromamba: bool):
    """
    Activate the environment with `eval $(coma shell)`

    Automatically installs the environment if it does not exist yet.
    """
    shell = os.path.basename(os.environ["SHELL"])
    exe = current_exe()
    prefix = current_prefix(exe)
    _install(prune=False, lazy=True, exe=exe, prefix=prefix)

    # Currently only works with conda or micromamba
    if not force_micromamba:
        conda_exe = safe_next(conda_executables())
        if conda_exe:
            print(f"eval \"$('{conda_exe}' shell.{shell} hook)\";")
            print(f"conda activate \"{prefix}\"")
            return

    micromamba_exe = next(micromamba_executables())
    print(f"eval \"$('{micromamba_exe}' shell hook -s {shell})\";")
    print(f"micromamba activate \"{prefix}\"")


if __name__ == "__main__":
    cli()
