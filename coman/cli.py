import os
import subprocess
import sys
from typing import List, Optional

import click

from coman.env import (env_info, env_init, env_install, env_lock, env_python_exe, env_search, env_show, env_uninstall)
from coman.system import (conda_exe, env_name, env_prefix, is_conda, is_micromamba, micromamba_exe, system_exe,
                          system_platform)
from coman.commands import spec
from coman.commands.utils import NaturalOrderGroup


@click.group(cls=NaturalOrderGroup)
@click.option('--conda/--no-conda', default=None)
@click.option('--conda-standalone/--no-conda-standalone', default=None)
@click.option('--mamba/--no-mamba', default=None)
@click.option('--micromamba/--no-micromamba', default=None)
@click.version_option()
def cli(conda: Optional[bool], conda_standalone: Optional[bool], mamba: Optional[bool], micromamba: Optional[bool]):
    if conda or conda_standalone or mamba or micromamba:
        system_exe(conda, conda_standalone, mamba, micromamba)


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
def uninstall():
    """
    Uninstall the environment

    You must deactivate the environment before you can remove it.
    """
    env_uninstall()


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
        print(file=sys.stderr)
        env_install(prune=prune, force=force, show=show)


cli.add_command(spec.list_deps)
cli.add_command(spec.add)
cli.add_command(spec.remove)
cli.add_command(spec.platform)
cli.add_command(spec.channel)


@cli.command()
@click.argument("pkg")
@click.option("--platform", default=None)
@click.option("--limit", type=int, default=5)
@click.option("--deps", default=False, is_flag=True)
def search(pkg: str, platform: Optional[str], limit: int, deps: bool):
    env_search(pkg, platform, limit, deps)


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

    # "conda run" only works with regular conda
    if is_conda(standalone=False):
        p = subprocess.run([system_exe(), "run", "--prefix", env_prefix(), "--no-capture-out", "--live-stream", *args])
        exit(p.returncode)

    # workaround for other backends
    exe_arg = "--micromamba" if is_micromamba() else ""
    cmd = f"eval $({os.path.abspath(sys.argv[0])} {exe_arg} shell --hook --quiet) && {' '.join(args)} && exit 0"
    p = subprocess.run(["/usr/bin/env", "bash", "-c", cmd])
    exit(p.returncode)


@cli.command()
@click.option("--hook", default=False, is_flag=True)
@click.option("--install/--no-install", default=True)
@click.option("--quiet", default=False, is_flag=True)
def shell(hook: bool, install: bool, quiet: bool):
    """
    Activate the environment with `eval $(coman shell)`

    Automatically installs the environment if it does not exist yet.
    """
    if install:
        env_install(quiet=True)

    from shellingham import detect_shell

    shell_name, shell_path = detect_shell()

    if hook:
        # Currently only works with conda or micromamba
        if not is_micromamba():
            exe = conda_exe(standalone=False)
            if exe:
                if hook:
                    if not quiet:
                        print("You can deactivate the environment with `conda deactivate`", file=sys.stderr)
                    print(f"eval \"$('{exe}' shell.{shell_name} hook)\" && conda activate \"{env_prefix()}\"")
                    exit(0)

        exe = micromamba_exe()
        if hook:
            if not quiet:
                print("You can deactivate the environment with `micromamba deactivate`", file=sys.stderr)
            print(f"eval \"$('{exe}' shell hook -s {shell_name})\" && micromamba activate \"{env_prefix()}\"")
            exit(0)

    else:
        import pexpect

        c = pexpect.spawn(shell_path, ["-i"])
        if shell_name == "zsh":
            c.setecho(False)

        shell_hook = f"eval $({os.path.abspath(sys.argv[0])} shell --hook --quiet)"
        c.sendline(shell_hook)
        c.interact(escape_character=None)
        c.close()
        exit(c.exitstatus)


if __name__ == "__main__":
    cli()
