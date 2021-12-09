import subprocess
import sys
from typing import List, Optional

import click
from click.core import Context

from coman.commands import add_spec_commands
from coman.env import env_info, env_init, env_install, env_lock, env_search, env_shell_hook, env_show, env_uninstall
from coman.system import Conda
from coman.spec import Specification

class AliasedGroup(click.Group):
    def get_command(self, ctx, cmd_name):
        aliases = {
            "ad": "add",
            "c": "channel",
            "i": "install",
            "l": "lock",
            "ls": "list",
            "p": "platform",
            "q": "query",
            "rm": "remove",
            "s": "shell",
            "u": "update",
        }
        cmd_name = aliases.get(cmd_name, cmd_name)
        return click.Group.get_command(self, ctx, cmd_name)


@click.group(cls=AliasedGroup, invoke_without_command=True)
@click.option('--mamba/--no-mamba', default=None)
@click.option('--conda/--no-conda', default=None)
@click.option('--conda-standalone/--no-conda-standalone', default=None)
@click.option('--micromamba/--no-micromamba', default=None)
@click.option("--platform", default=False, is_flag=True)
@click.option("--root", default=False, is_flag=True)
@click.option("--conda-bin", default=False, is_flag=True)
@click.option("--name", default=False, is_flag=True)
@click.option("--prefix", default=False, is_flag=True)
@click.option("--python-bin", default=False, is_flag=True)
@click.version_option()
@click.pass_context
def cli(
    ctx: Context,
    mamba: Optional[bool],
    conda: Optional[bool],
    conda_standalone: Optional[bool],
    micromamba: Optional[bool],
    platform: bool,
    root: bool,
    conda_bin: bool,
    name: bool,
    prefix: bool,
    python_bin: bool,
):
    conda_ = ctx.obj = Conda(mamba, conda, conda_standalone, micromamba)
    spec = Specification()

    if root:
        return print(conda_.root)
    if conda_bin:
        return print(conda_.exe)
    if platform:
        return print(conda_.env.platform)
    if name:
        return print(conda_.env.name)
    if prefix:
        return print(conda_.env.prefix)
    if python_bin:
        return print(conda_.env.python)

    if ctx.invoked_subcommand is None:
        env_info(conda_, spec)


@cli.command()
@click.option("--force", default=False, is_flag=True)
@click.pass_obj
def init(conda: Conda, force: bool):
    """
    Initialize a new environment.yml
    """
    spec = Specification()
    env_init(conda, spec, force=force)


@cli.command()
@click.pass_obj
def lock(conda: Conda):
    """
    Lock the package specifications
    """
    spec = Specification()
    env_lock(conda, spec)


@cli.command()
@click.option("--prune/--no-prune", default=None)
@click.option("--force", default=False, is_flag=True)
@click.option("--show/--no-show", default=True)
@click.pass_obj
def install(conda: Conda, prune: Optional[bool], force: bool, show: bool):
    """
    Install the environment based on the lock file
    """
    spec = Specification()
    env_install(conda, spec, prune=prune, force=force, show=show)


@cli.command()
@click.pass_obj
def uninstall(conda: Conda):
    """
    Uninstall the environment

    You must deactivate the environment before you can remove it.
    """
    env_uninstall(conda)


@cli.command()
@click.option("--install/--no-install", default=None)
@click.option("--prune/--no-prune", default=None)
@click.option("--force", default=False, is_flag=True)
@click.option("--show/--no-show", default=True)
@click.pass_obj
def update(conda: Conda, install: Optional[bool], prune: Optional[bool], force: bool, show: bool):
    """
    Update the lock file(s) and install the new environment
    """
    spec = Specification()
    env_lock(conda, spec)
    if install is None:
        install = conda.env.prefix.exists()
    if install:
        print(file=sys.stderr)
        env_install(conda, spec, prune=prune, force=force, show=show)


@cli.command()
@click.argument("pkg")
@click.option("--platform", default=None)
@click.option("--limit", type=int, default=5)
@click.option("--deps", default=False, is_flag=True)
@click.pass_obj
def query(conda: Conda, pkg: str, platform: Optional[str], limit: int, deps: bool):
    spec = Specification()
    env_search(conda, spec, pkg, platform, limit, deps)


@cli.command()
@click.argument("query", nargs=-1)
@click.option("--install/--no-install", default=True)
@click.option("--all", default=False, is_flag=True)
@click.option("--pip/--no-pip", default=None)
@click.pass_obj
def show(conda: Conda, query: List[str], install: bool, all: bool, pip: Optional[bool]):
    """
    Show packages in the current environment
    """
    spec = Specification()
    if install:
        env_install(conda, spec, quiet=True)
    env_show(conda, spec, query, all, pip)


@cli.command()
@click.option("--install/--no-install", default=True)
@click.argument("args", nargs=-1)
@click.pass_obj
def run(conda: Conda, install: bool, args: List[str]):
    """
    Run a command within the environment

    Automatically installs the environment if it does not exist yet.
    """
    spec = Specification()
    if install:
        env_install(conda, spec, quiet=True)
    conda.env.run(args)


def run_bash(conda: Optional[Conda] = None, spec: Optional[Specification] = None, install: bool = True, args: List[str] = None):
    conda = conda or Conda()
    spec = spec or Specification()
    if args is None:
        args = sys.argv[1:]
    if install:
        env_install(conda, spec, quiet=True)
    conda.env.run(["bash", *args])


@cli.command()
@click.option("--install/--no-install", default=True)
@click.argument("args", nargs=-1)
@click.pass_obj
def bash(conda: Conda, install: bool, args: List[str]):
    spec = Specification()
    run_bash(conda, spec, install, args)


def run_python(conda: Optional[Conda] = None, spec: Optional[Specification] = None, install: bool = True, args: List[str] = None):
    conda = conda or Conda()
    spec = spec or Specification()
    if args is None:
        args = sys.argv[1:]
    if install:
        env_install(conda, spec, quiet=True)
    print(click.style("Python:", fg="cyan"), click.style(conda.env.python, fg="blue") + "\n", file=sys.stderr)
    exit(subprocess.run([conda.env.python, *args]).returncode)


@cli.command()
@click.option("--install/--no-install", default=True)
@click.argument("args", nargs=-1)
@click.pass_obj
def python(conda: Conda, install: bool, args: List[str]):
    spec = Specification()
    run_python(conda, spec, install, args)


@cli.command()
@click.option("--shell", default=None)
@click.option("--hook", default=False, is_flag=True)
@click.option("--install/--no-install", default=True)
@click.option("--quiet", default=False, is_flag=True)
@click.pass_obj
def shell(conda: Conda, shell: Optional[str], hook: bool, install: bool, quiet: bool):
    """
    Activate the environment with `eval $(coman shell)`

    Automatically installs the environment if it does not exist yet.
    """
    spec = Specification()
    if install:
        env_install(conda, spec, quiet=True)

    if hook:
        env_shell_hook(conda, quiet, shell_type=shell or "posix")
        exit(0)

    from shellingham import detect_shell
    shell_type, shell_path = detect_shell()
    exit(subprocess.run([shell_path, "-c", f"{conda.env.shell_hook(shell_type)}; {shell_path} -i"]).returncode)

add_spec_commands(cli)


if __name__ == "__main__":
    cli()
