import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import click
from ensureconda.resolve import (conda_executables, conda_standalone_executables, mamba_executables,
                                 micromamba_executables, safe_next)

from coman.env import change_spec, env_install, env_lock, env_python_exe, env_python_version, env_uninstall
from coman.spec import conda_lock_file, conda_outdated, pip_outdated, spec_file
from coman.system import (MIN_CONDA_VERSION, MIN_MAMBA_VERSION, conda_exe, env_name, env_prefix, envs_dir,
                          is_micromamba, conda_search, system_exe, system_platform)

from ._version import __version__


class NaturalOrderGroup(click.Group):
    def list_commands(self, _):
        return self.commands.keys()


@click.group(cls=NaturalOrderGroup)
@click.option('--mamba', default=False, is_flag=True)
@click.option('--micromamba', default=False, is_flag=True)
@click.option('--conda', default=False, is_flag=True)
@click.version_option()
def cli(mamba: bool, micromamba: bool, conda: bool):
    if mamba or micromamba or conda:
        system_exe(mamba, micromamba, conda)


@cli.command()
@click.option("--name", default=False, is_flag=True)
@click.option("--prefix", default=False, is_flag=True)
@click.option("--platform", default=False, is_flag=True)
@click.option("--exe", default=False, is_flag=True)
@click.option("--python", default=False, is_flag=True)
def info(name: bool, prefix: bool, platform: bool, exe: bool, python: bool):
    """
    Info about environment and current system
    """
    from ensureconda.api import (determine_conda_version, determine_mamba_version, determine_micromamba_version)
    from ensureconda.installer import install_conda_exe, install_micromamba

    if name:
        return print(env_name())
    if prefix:
        return print(env_prefix())
    if platform:
        return print(system_platform())
    if exe:
        return print(system_exe())
    if python:
        return print(env_python_exe())

    sys_prefix = env_prefix()
    sys_platform = system_platform()

    print("Current environment")
    sys_status = "up-to-date"
    if not spec_file().exists():
        sys_status = "no environment.yml (run `coman init`)"
    elif not conda_lock_file().exists():
        sys_status = f"no lock file for this platform (run `coman lock`)"
    elif not sys_prefix.exists():
        sys_status = "not installed (run `coman install`)"
    elif conda_outdated():
        sys_status = "env outdated (run `coman install`)"
    elif pip_outdated():
        sys_status = "pip outdated (run `coman install`)"

    print(f"> Prefix:   {sys_prefix}")
    print(f"> Platform: {sys_platform}")
    print(f"> Status:   {sys_status}")
    if sys_prefix.exists():
        print(f"> Python:   {env_python_version()}")

    print("\nCoMan")
    print(f"> Version:  {__version__}")
    py = sys.version_info
    print(f"> Python:   {py.major}.{py.minor}.{py.micro}")
    print(f"> Envs dir: {envs_dir()}")

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
    if spec_file().exists():
        print(f"Specification file `{spec_file()}` already exists", file=sys.stderr)
        exit(1)

    print("Creating `environment.yml`")
    pkg = conda_search("python", ["conda-forge"])
    pkg = f"{pkg['name']} >={pkg['version']}"

    with open(spec_file(), "w") as f:
        f.write(f"channels:\n- conda-forge\n\nplatforms:\n- {system_platform()}\n\ndependencies:\n- {pkg}\n")

    env_lock()


@cli.command()
def lock():
    """
    Lock the package specifications
    """
    env_lock()


@cli.command()
def uninstall():
    """
    Uninstall the environment

    You must deactivate the environment before you can remove it.
    """
    env_uninstall()


@cli.command()
@click.option("--prune/--no-prune", default=None)
@click.option("--force", default=False, is_flag=True)
def install(prune: Optional[bool], force: bool):
    """
    Install the environment based on the lock file
    """
    env_install(prune=prune, force=force)


@cli.command()
@click.option("--prune/--no-prune", default=None)
@click.option("--force", default=False, is_flag=True)
def update(prune: Optional[bool], force: bool):
    """
    Update the lock file(s) and install the new environment
    """
    env_lock()
    env_install(prune=prune, force=force)


@cli.command()
@click.argument("pkgs", nargs=-1)
@click.option("--prune/--no-prune", default=None)
@click.option("--pip", default=False, is_flag=True)
def add(pkgs: List[str], prune: Optional[bool], pip: bool):
    """
    Add a package to environment.yml, update the lock file(s) and install the environment
    """
    # TODO Add platform filters with i.e. "# [linux64]" filter_platform_selectors()
    change_spec(add_pkgs=pkgs, prune=prune, pip=pip)


@cli.command()
@click.argument("pkgs", nargs=-1)
@click.option("--prune/--no-prune", default=None)
@click.option("--pip", default=False, is_flag=True)
def remove(pkgs: List[str], prune: Optional[bool], pip: bool):
    """
    Remove a package from environment.yml, update the lock file(s) and install the environment
    """
    change_spec(remove_pkgs=pkgs, prune=prune, pip=pip)


@cli.command()
@click.argument("query", nargs=-1)
def show(query: List[str]):
    """
    Show packages in the current environment
    """
    exe = system_exe()
    subprocess.run([exe, "list", "--prefix", env_prefix(), *query, "--quiet"])


@cli.command()
@click.argument("args", nargs=-1)
def run(args):
    """
    Run a command within the environment

    Automatically installs the environment if it does not exist yet.
    """
    env_install()

    # Currently only works with conda
    exe = conda_exe(standalone=False)
    if not exe:
        print("This command requires a Conda installation", file=sys.stderr)
        exit(1)
    subprocess.run([exe, "run", "--prefix", env_prefix(), "--no-capture-out", "--live-stream", *args])


@cli.command()
def shell():
    """
    Activate the environment with `eval $(coman shell)`

    Automatically installs the environment if it does not exist yet.
    """
    env_install()

    shell = Path(os.environ["SHELL"]).name

    # Currently only works with conda or micromamba
    if not is_micromamba():
        exe = conda_exe(standalone=False)
        if exe:
            print("You can deactivate the environment with `conda deactivate`", file=sys.stderr)
            print(f"eval \"$('{exe}' shell.{shell} hook)\";")
            print(f"conda activate \"{env_prefix()}\"")
            exit(0)

    exe = next(micromamba_executables())
    print("You can deactivate the environment with `micromamba deactivate`", file=sys.stderr)
    print(f"eval \"$('{exe}' shell hook -s {shell})\";")
    print(f"micromamba activate \"{env_prefix()}\"")


if __name__ == "__main__":
    cli()
