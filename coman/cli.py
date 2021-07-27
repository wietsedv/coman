import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import click

from coman.env import (change_spec, env_info, env_init, env_install, env_lock, env_python_exe, env_search, env_show,
                       env_uninstall)
from coman.system import (conda_exe, env_name, env_prefix, is_micromamba, micromamba_exe, system_exe, system_platform)


class NaturalOrderGroup(click.Group):
    def list_commands(self, _):
        return self.commands.keys()


@click.group(cls=NaturalOrderGroup)
@click.option('--conda/--no-conda', default=None)
@click.option('--mamba/--no-mamba', default=None)
@click.option('--micromamba/--no-micromamba', default=None)
@click.version_option()
def cli(conda: Optional[bool], mamba: Optional[bool], micromamba: Optional[bool]):
    if conda or mamba or micromamba:
        system_exe(conda, mamba, micromamba)


@cli.command()
@click.option("--name", default=False, is_flag=True)
@click.option("--prefix", default=False, is_flag=True)
@click.option("--platform", default=False, is_flag=True)
@click.option("--conda-exe", default=False, is_flag=True)
@click.option("--python-exe", default=False, is_flag=True)
def info(name: bool, prefix: bool, platform: bool, conda_exe: bool, python_exe: bool):
    """
    Info about environment and current system
    """
    if name:
        return print(env_name())
    if prefix:
        return print(env_prefix())
    if platform:
        return print(system_platform())
    if conda_exe:
        return print(system_exe())
    if python_exe:
        return print(env_python_exe())
    env_info()


@cli.command()
def init():
    """
    Initialize a new environment.yml
    """
    env_init()


@cli.command()
def lock():
    """
    Lock the package specifications
    """
    env_lock()


@cli.command()
@click.option("--prune/--no-prune", default=None)
@click.option("--force", default=False, is_flag=True)
@click.option("--show/--no-show", default=True)
def install(prune: Optional[bool], force: bool, show: bool):
    """
    Install the environment based on the lock file
    """
    env_install(prune=prune, force=force, show=show)


@cli.command()
@click.option("--install/--no-install", default=True)
@click.option("--prune/--no-prune", default=None)
@click.option("--force", default=False, is_flag=True)
@click.option("--show/--no-show", default=True)
def update(install: bool, prune: Optional[bool], force: bool, show: bool):
    """
    Update the lock file(s) and install the new environment
    """
    env_lock()
    if install:
        print()
        env_install(prune=prune, force=force, show=show)


@cli.command()
@click.argument("pkgs", nargs=-1)
@click.option("--pip", default=False, is_flag=True)
@click.option("--update/--no-update", default=True)
@click.option("--install/--no-install", default=None)
@click.option("--prune/--no-prune", default=None)
@click.option("--force", default=False, is_flag=True)
@click.option("--show/--no-show", default=True)
def add(pkgs: List[str], pip: bool, update: bool, install: Optional[bool], prune: Optional[bool], force: bool,
        show: bool):
    """
    Add a package to environment.yml, update the lock file(s) and install the environment
    """
    change_spec(add_pkgs=pkgs,
                remove_pkgs=[],
                pip=pip,
                update=update,
                install=install,
                prune=prune,
                force=force,
                show=show)


@cli.command()
@click.argument("pkgs", nargs=-1)
@click.option("--pip", default=False, is_flag=True)
@click.option("--update/--no-update", default=True)
@click.option("--install/--no-install", default=None)
@click.option("--prune/--no-prune", default=None)
@click.option("--force", default=False, is_flag=True)
@click.option("--show/--no-show", default=True)
def remove(pkgs: List[str], pip: bool, update: bool, install: Optional[bool], prune: Optional[bool], force: bool,
           show: bool):
    """
    Remove a package from environment.yml, update the lock file(s) and install the environment
    """
    change_spec(add_pkgs=[],
                remove_pkgs=pkgs,
                pip=pip,
                update=update,
                install=install,
                prune=prune,
                force=force,
                show=show)


@cli.command()
def uninstall():
    """
    Uninstall the environment

    You must deactivate the environment before you can remove it.
    """
    env_uninstall()


@cli.command()
@click.argument("query", nargs=-1)
@click.option("--install/--no-install", default=True)
@click.option("--deps/--no-deps", default=False, help="Include installed dependencies of your packages.")
@click.option("--pip/--no-pip", default=None)
def show(query: List[str], install: bool, deps: bool, pip: Optional[bool]):
    """
    Show packages in the current environment
    """
    if install:
        env_install(quiet=True)
    env_show(query, deps, pip)


@cli.command()
@click.argument("args", nargs=-1)
@click.option("--install/--no-install", default=True)
def run(args, install: bool):
    """
    Run a command within the environment

    Automatically installs the environment if it does not exist yet.
    """
    if install:
        env_install(quiet=True)

    # Currently only works with conda
    exe = conda_exe(standalone=False)
    if not exe:
        print("This command requires a Conda installation", file=sys.stderr)
        exit(1)
    subprocess.run([exe, "run", "--prefix", env_prefix(), "--no-capture-out", "--live-stream", *args])


@cli.command()
@click.option("--install/--no-install", default=True)
def shell(install: bool):
    """
    Activate the environment with `eval $(coman shell)`

    Automatically installs the environment if it does not exist yet.
    """
    if install:
        env_install(quiet=True)

    shell = Path(os.environ["SHELL"]).name

    # Currently only works with conda or micromamba
    if not is_micromamba():
        exe = conda_exe(standalone=False)
        if exe:
            print("You can deactivate the environment with `conda deactivate`", file=sys.stderr)
            print(f"eval \"$('{exe}' shell.{shell} hook)\";")
            print(f"conda activate \"{env_prefix()}\"")
            exit(0)

    exe = micromamba_exe()
    print("You can deactivate the environment with `micromamba deactivate`", file=sys.stderr)
    print(f"eval \"$('{exe}' shell hook -s {shell})\";")
    print(f"micromamba activate \"{env_prefix()}\"")


@cli.command()
@click.argument("pkg")
@click.option("--platform", default=None)
@click.option("--limit", type=int, default=5)
def search(pkg: str, platform: Optional[str], limit: int):
    env_search(pkg, platform, limit)


if __name__ == "__main__":
    cli()
