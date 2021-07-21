from os import PathLike
import os
from typing import Callable, Iterator, List, Optional, Tuple
import subprocess
import json
from hashlib import md5
from pathlib import Path
import sys
import re

import click
from conda_lock.conda_lock import determine_conda_executable
from ensureconda.installer import install_conda_exe
from ensureconda.resolve import platform_subdir

from ._version import __version__

ENV_HASH_PATTERN = re.compile(r"^# env_hash: (.*)$")


def safe_next(it: Iterator[PathLike]):
    try:
        return next(it)
    except StopIteration:
        return None


def is_mamba(exe: PathLike) -> bool:
    return str(exe).endswith("/mamba")


def current_exe():
    return Path(determine_conda_executable(None, mamba=True, micromamba=False))


def current_envs_dir(exe: PathLike):
    res = json.loads(subprocess.check_output([exe, "info", "--json"]))
    return Path(res["envs_dirs"][0])


def current_name():
    current_dir = os.path.normpath(os.getcwd())
    current_basename = os.path.basename(current_dir)
    hash = md5(current_dir.encode("utf-8")).hexdigest()[:8]
    return f"{current_basename}-{hash}"


def current_prefix(exe: PathLike):
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


def current_platforms():
    if not os.path.exists("environment.yml"):
        return []

    import ruamel.yaml
    with open("environment.yml") as f:
        env = ruamel.yaml.safe_load(f)
    return env.get("platforms", [platform_subdir()])


def repoquery_search(exe: PathLike, spec: str, channels: List[str]):
    args = []
    for c in channels:
        args.extend(["-c", c])
    res = json.loads(subprocess.check_output([exe, "repoquery", "search", spec, *args, "--json"]))["result"]
    if res["msg"]:
        print(res["msg"])
        exit(1)
    pkg = max(res["pkgs"], key=lambda pkg: pkg["timestamp"])
    return pkg


@click.group()
@click.version_option()
def cli():
    pass


@cli.command()
@click.option("--name", default=False, is_flag=True)
@click.option("--prefix", default=False, is_flag=True)
@click.option("--platform", default=False, is_flag=True)
def info(name, prefix, platform):
    if name:
        return print(current_name())
    if prefix:
        exe = current_exe()
        return print(current_prefix(exe))
    if platform:
        return print(platform_subdir())

    from ensureconda.resolve import (mamba_executables, micromamba_executables, conda_executables,
                                     conda_standalone_executables)
    from ensureconda.api import (determine_mamba_version, determine_micromamba_version, determine_conda_version)
    from ensureconda.installer import install_micromamba

    exe = current_exe()

    prefix = current_prefix(exe)
    print("Current environment:")
    print(f"> Prefix: {prefix} [{'ok' if prefix.exists() else 'not found'}]")
    print(f"> Platform: {platform_subdir()}")

    print("\nPoco:")
    print(f"> Version:  v{__version__}")
    py = sys.version_info
    print(f"> Python:   v{py.major}.{py.minor}.{py.micro}")
    print(f"> Envs dir: {current_envs_dir(exe)}")

    print("\nConda:")
    mamba_exe = safe_next(mamba_executables())
    mamba_ver = "n/a"
    if mamba_exe:
        mamba_ver = f"v{determine_mamba_version(mamba_exe)} [{mamba_exe}]"
    print(f"> Mamba:            {mamba_ver}")

    micromamba_exe = safe_next(micromamba_executables()) or install_micromamba()
    micromamba_ver = "n/a"
    if micromamba_exe:
        micromamba_ver = f"v{determine_micromamba_version(micromamba_exe)} [{micromamba_exe}]"
    print(f"> Micromamba:       {micromamba_ver}")

    conda_exe = safe_next(conda_executables())
    conda_ver = "n/a"
    if conda_exe:
        conda_ver = f"v{determine_conda_version(conda_exe)} [{conda_exe}]"
    print(f"> Conda:            {conda_ver}")

    try:
        condastandalone_exe = safe_next(conda_standalone_executables()) or install_conda_exe()
    except IndexError:
        condastandalone_exe = None
    condastandalone_ver = "n/a"
    if condastandalone_exe:
        condastandalone_ver = f"v{determine_conda_version(condastandalone_exe)} [{condastandalone_exe}]"
    print(f"> Conda standalone: {condastandalone_ver}")


@cli.command()
@click.argument("query", nargs=-1)
def list(query: List[str]):
    exe = current_exe()
    subprocess.run([exe, "list", "--prefix", current_prefix(exe), *query, "--quiet"])


def _lock(platforms: List[str] = None):
    from conda_lock.conda_lock import run_lock
    from glob import glob

    platforms = platforms or current_platforms()

    lock_template = "conda-{platform}.lock"
    lock_files = [lock_template.format(platform=p) for p in platforms]
    for lock_file in glob("conda-*.lock"):
        if lock_file not in lock_files:
            os.remove(lock_file)

    run_lock(
        environment_files=[Path("environment.yml")],
        conda_exe=None,
        platforms=platforms or [platform_subdir()],
        mamba=True,
        micromamba=False,
        include_dev_dependencies=True,
        channel_overrides=None,
        kinds=["explicit"],
        filename_template=lock_template,
    )


def extract_env_hash(lock: str) -> str:
    for line in lock.strip().split("\n"):
        m = ENV_HASH_PATTERN.search(line)
        if m:
            return m.group(1)
    raise RuntimeError("Cannot find env_hash in lockfile")


def _install(prune: bool, lazy: bool = False, **kwargs):
    from conda_lock.conda_lock import do_validate_platform

    exe = kwargs.get("exe", current_exe())
    prefix = kwargs.get("prefix", current_prefix(exe))

    lock_file = Path(f"conda-{platform_subdir()}.lock")
    if not lock_file.exists():
        _lock()
    with open(lock_file) as f:
        lock_str = f.read()

    do_validate_platform(lock_str)
    env_hash = extract_env_hash(lock_str)
    if lazy and env_hash == current_env_hash(prefix):
        return

    args = [
        str(exe),
        "create" if prune or not prefix.exists() else "update",
        "--file",
        str(lock_file),
        "--prefix",
        str(prefix),
        "--yes",
    ]
    p = subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        print(p.stdout)
        print(p.stderr)
        print(f"Could not perform conda install using {lock_file} lock file into {prefix}")
        exit(1)

    save_env_hash(prefix, env_hash)


@cli.command()
def lock():
    _lock()


@cli.command()
@click.option("--prune", default=False, is_flag=True)
def install(prune: bool):
    _install(prune)


@cli.command()
@click.option("--prune", default=False, is_flag=True)
def update(prune: bool):
    _lock()
    _install(prune)


def load_env() -> Tuple[dict, Callable]:
    import ruamel.yaml

    environment_file = Path("environment.yml")
    if not environment_file.exists():
        raise FileNotFoundError(f"{environment_file} not found")

    yaml = ruamel.yaml.YAML()
    with open(environment_file) as f:
        env = yaml.load(f)

    if "channels" not in env:
        env["channels"] = ["conda-forge"]
    if "dependencies" not in env:
        env["dependencies"] = ["python"]

    def save_func():
        with open(environment_file, "w") as f:
            yaml.dump(env, f)

    return env, save_func


@cli.command()
@click.argument("specs", nargs=-1)
@click.option("--update/--no-update", default=True, is_flag=True)
@click.option("--prune", default=False, is_flag=True)
def add(specs: List[str], update: bool, prune: bool):
    env, save_func = load_env()
    exe = current_exe()

    dep_names = [spec.split(" ")[0] for spec in env["dependencies"]]

    changed = False
    for spec in specs:
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

    if changed:
        save_func()
        if update:
            _lock(env.get("platforms", [platform_subdir()]))
            _install(prune)


@cli.command()
@click.argument("specs", nargs=-1)
@click.option("--update/--no-update", default=True, is_flag=True)
@click.option("--prune", default=False, is_flag=True)
def remove(specs: List[str], update: bool, prune: bool):
    env, save_func = load_env()

    dep_names = [spec.split(" ")[0] for spec in env["dependencies"]]

    changed = False
    for spec in specs:
        if spec not in dep_names:
            print(f"Dependency {spec} not found")
            continue
        i = dep_names.index(spec)
        env["dependencies"].pop(i)
        dep_names.pop(i)
        changed = True

    if changed:
        save_func()
        if update:
            _lock(env.get("platforms", [platform_subdir()]))
            _install(prune)


@cli.command()
def shell():
    from ensureconda.resolve import conda_executables, micromamba_executables

    shell = os.path.basename(os.environ["SHELL"])
    exe = current_exe()
    prefix = current_prefix(exe)
    _install(prune=False, lazy=True, exe=exe, prefix=prefix)

    # shell only seems to work properly with conda and micromamba
    conda_exe = safe_next(conda_executables())
    if conda_exe:
        print(f"eval \"$('{conda_exe}' shell.{shell} hook)\";")
        print(f"conda activate \"{prefix}\"")
        return

    micromamba_exe = next(micromamba_executables())
    print(f"eval \"$('{micromamba_exe}' shell hook -s {shell})\";")
    print(f"micromamba activate \"{prefix}\"")


@cli.command()
@click.argument("args", nargs=-1)
def run(args):
    exe = current_exe()
    prefix = current_prefix(exe)
    _install(prune=False, lazy=True, exe=exe, prefix=prefix)

    if is_mamba(exe):
        exe = determine_conda_executable(None, mamba=False, micromamba=False)

    subprocess.run([exe, "run", "--prefix", prefix, "--no-capture-out", "--live-stream", *args])


if __name__ == "__main__":
    cli()
