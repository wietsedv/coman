from collections import OrderedDict
from coman.utils import COLORS, format_pkg_line, pkg_col_lengths
import sys
from coman.env import env_install, env_lock
from typing import List, Optional

import click

from coman.spec import PLATFORMS, edit_spec_file, spec_channels, spec_dependencies, spec_platforms
from coman.system import conda_pkg_info, env_prefix, pypi_pkg_info
from coman.commands.utils import NaturalOrderGroup


def change_dependencies(*, add_pkgs: List[str], remove_pkgs: List[str], pip: bool):
    spec_data, save_spec_file = edit_spec_file()

    def _dep_names(deps):
        return [pkg.split(" ")[0] if not isinstance(pkg, OrderedDict) else None for pkg in deps]

    deps = spec_data["dependencies"]
    dep_names = _dep_names(deps)

    def _add_pkg(pkg: str, pip: bool):
        if pip:
            if "@" in pkg:
                name, ver = pkg.split("@")
                pkg_info = {"name": name, "version": ver}
                pkg_spec = f"{name} {ver}"
            else:
                pkg_info = pypi_pkg_info(pkg)
                name, ver = pkg_info["name"], pkg_info["version"]
                pkg_spec = f"{name} >={ver}"
        else:
            pkg_info = conda_pkg_info(pkg, channels=spec_data["channels"])
            name, ver = pkg_info["name"], pkg_info["version"]
            pkg_spec = f"{name} >={ver}"

        i = len(deps)
        if name in dep_names:
            i = dep_names.index(name)
            if deps[i] == pkg_spec:
                return False
            deps.pop(i)

        deps.insert(i, pkg_spec)
        dep_names.insert(i, name)

        pkg_fmt = f"{click.style(name, fg='cyan' if pip else 'green')} ({click.style(ver, fg='blue')})"
        print(click.style("   spec:", fg="bright_white"), f"Added {pkg_fmt} to dependencies", file=sys.stderr)
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

    changed = False
    conda = not pip
    if pip:
        if "pip" not in dep_names:
            conda = _add_pkg("pip", pip=False)
            changed = True

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
    return changed and conda, changed and pip


def save_env(update: Optional[bool],
             install: Optional[bool],
             prune: Optional[bool],
             force: bool,
             show: bool,
             conda: bool = True,
             pip: bool = False):
    if update is not False:
        print(file=sys.stderr)
        env_lock(conda=update or conda, pip=update or pip)

    if install is None:
        install = env_prefix().exists()

    if update is not False and install:
        print(file=sys.stderr)
        env_install(prune=prune, force=force, show=show)


def spec_list_add(items: List[str], key: str, item_key: str, prepend: bool=False):
    spec_data, save_spec_file = edit_spec_file()

    changed = False
    for item in (reversed(items) if prepend else items):
        if item in spec_data[key]:
            continue
        if prepend:
            spec_data[key].insert(0, item)
        else:
            spec_data[key].append(item)
        print(click.style("   spec:", fg="bright_white"),
              f"Added {click.style(item, fg=COLORS[item_key])} to {key}",
              file=sys.stderr)
        changed = True

    if not changed:
        print(click.style("   spec:", fg="bright_white"),
              click.style(f"No new {key} added", fg="yellow"),
              file=sys.stderr)

    save_spec_file()
    return changed


def spec_list_remove(items: List[str], key: str, item_key: str):
    spec_data, save_spec_file = edit_spec_file()

    changed = False
    for item in items:
        if item not in spec_data[key]:
            continue
        spec_data[key].remove(item)
        print(click.style("   spec:", fg="bright_white"),
              f"Removed {click.style(item, fg=COLORS[item_key])} from {key}",
              file=sys.stderr)
        changed = True

    if not changed:
        print(click.style("   spec:", fg="bright_white"),
              click.style(f"No {key} removed", fg="yellow"),
              file=sys.stderr)

    save_spec_file()
    return changed


def add_update_options(fn):
    options = [
        click.option("--update/--no-update", default=None),
        click.option("--install/--no-install", default=None),
        click.option("--prune/--no-prune", default=None),
        click.option("--force", default=False, is_flag=True),
        click.option("--show/--no-show", default=True)
    ]
    for option in reversed(options):
        fn = option(fn)
    return fn


@click.command("list")
def list_deps():
    conda_pkgs, pip_pkgs = spec_dependencies()
    col_lengths = pkg_col_lengths(conda_pkgs + pip_pkgs, ["name", "version", "comment"])

    click.secho("Conda", fg="green", bold=True)
    for pkg_info in conda_pkgs:
        print(f"- {format_pkg_line(pkg_info, col_lengths)}")

    if pip_pkgs:
        click.secho("\nPip", fg="cyan", bold=True)
        for pkg_info in pip_pkgs:
            print(f"- {format_pkg_line(pkg_info, col_lengths)}")


@click.command()
@click.argument("pkgs", nargs=-1)
@click.option("--pip", default=False, is_flag=True)
@add_update_options
def add(pkgs: List[str], pip: bool, update: Optional[bool], install: Optional[bool], prune: Optional[bool], force: bool,
        show: bool):
    """
    Add a package to environment.yml, update the lock file(s) and install the environment
    """
    conda, pip = change_dependencies(add_pkgs=pkgs, remove_pkgs=[], pip=pip)
    if conda or pip or update:
        save_env(update, install, prune, force, show, conda=conda, pip=pip)


@click.command()
@click.argument("pkgs", nargs=-1)
@click.option("--pip", default=False, is_flag=True)
@add_update_options
def remove(pkgs: List[str], pip: bool, update: Optional[bool], install: Optional[bool], prune: Optional[bool],
           force: bool, show: bool):
    """
    Remove a package from environment.yml, update the lock file(s) and install the environment
    """
    conda, pip = change_dependencies(add_pkgs=[], remove_pkgs=pkgs, pip=pip)
    if conda or pip or update:
        prune = True if prune is None else prune
        save_env(update, install, prune, force, show, conda=conda, pip=pip)


@click.group(cls=NaturalOrderGroup)
def platform():
    pass


@platform.command("list")
def platform_list():
    platforms = spec_platforms()
    col_lengths = pkg_col_lengths(platforms, ["platform", "comment"])
    for platform in platforms:
        print(f"- {format_pkg_line(platform, col_lengths)}")


@platform.command("add")
@click.argument("platforms", nargs=-1)
@add_update_options
def platform_add(platforms: List[str], update: Optional[bool], install: Optional[bool], prune: Optional[bool],
                 force: bool, show: bool):
    for platform in platforms:
        if platform not in PLATFORMS:
            click.secho(f"Cannot add unknown platform '{click.style(platform, bold=True)}'", fg="red", file=sys.stderr)
            exit(1)
    changed = spec_list_add(platforms, "platforms", "platform")
    if changed or update:
        save_env(update, install, prune, force, show)


@platform.command("remove")
@click.argument("platforms", nargs=-1)
@add_update_options
def platform_remove(platforms: List[str], update: Optional[bool], install: Optional[bool], prune: Optional[bool],
                    force: bool, show: bool):
    changed = spec_list_remove(platforms, "platforms", "platform")
    if changed or update:
        save_env(update, install, prune, force, show)


@click.group(cls=NaturalOrderGroup)
def channel():
    pass


@channel.command("list")
def channel_list():
    channels = spec_channels()
    col_lengths = pkg_col_lengths(channels, ["channel", "comment"])
    for channel in channels:
        print(f"- {format_pkg_line(channel, col_lengths)}")


@channel.command("add")
@click.argument("channels", nargs=-1)
@add_update_options
def channel_add(channels: List[str], update: Optional[bool], install: Optional[bool], prune: Optional[bool],
                force: bool, show: bool):
    changed = spec_list_add(channels, "channels", "channel", prepend=True)
    if changed or update:
        save_env(update, install, prune, force, show)


@channel.command("remove")
@click.argument("channels", nargs=-1)
@add_update_options
def channel_remove(channels: List[str], update: Optional[bool], install: Optional[bool], prune: Optional[bool],
                   force: bool, show: bool):
    changed = spec_list_remove(channels, "channels", "channel")
    if changed or update:
        save_env(update, install, prune, force, show)
