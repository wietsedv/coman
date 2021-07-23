import os
from glob import glob
import sys
from typing import List

from conda_lock.conda_lock import create_lockfile_from_spec
from conda_lock.src_parser.environment_yaml import parse_environment_file

from coman.spec import edit_spec_file, lock_env_hash, lock_file, spec_file, spec_platforms
from coman.system import env_prefix, env_prefix_hash, repoquery_search, run_exe, system_exe, system_platform


def env_lock():
    platforms = spec_platforms() or [system_platform()]
    new_lock_paths = [str(lock_file(p)) for p in platforms]
    for lock_path in glob(str(lock_file("*"))):
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
        with open(lock_file(platform), "w") as f:
            f.write("\n".join(lock_contents) + "\n")


def env_install(prune: bool = False, lazy: bool = False):
    sys_platform = system_platform()
    lock_path = lock_file(sys_platform)
    if not lock_path.exists():
        platforms = spec_platforms()
        if platforms and sys_platform not in platforms:
            raise RuntimeError(f"platform {sys_platform} is not available")
        env_lock()

    prefix = env_prefix()
    new_env_hash = lock_env_hash(lock_path)
    if lazy and new_env_hash == env_prefix_hash(prefix):
        return

    print(f"Installing environment to {prefix}", file=sys.stderr)
    args = [
        "create" if prune or not prefix.exists() else "update",
        "--file",
        lock_path,
        "--prefix",
        prefix,
        "--yes",
    ]
    if not run_exe(args):
        raise RuntimeError(f"\nCould not install {lock_path} into {prefix}")

    with open(prefix / "env_hash.txt", "w") as f:
        f.write(new_env_hash)


def env_uninstall():
    run_exe(["env", "remove", "--prefix", env_prefix()])


def change_spec(add_pkgs: List[str] = [], remove_pkgs: List[str] = []):
    spec_data, save_spec_file = edit_spec_file()

    dep_names = [spec.split(" ")[0] for spec in spec_data["dependencies"]]
    changed = False

    # Add
    for pkg in add_pkgs:
        pkg = repoquery_search(pkg, spec_data["channels"])
        name = pkg['name']
        pkg = f"{name} >={pkg['version']}"

        i = len(spec_data["dependencies"])
        if name in dep_names:
            i = dep_names.index(name)
            if spec_data["dependencies"][i] == pkg:
                continue
            spec_data["dependencies"].pop(i)

        spec_data["dependencies"].insert(i, pkg)
        dep_names.insert(i, name)
        print(f"Added '{pkg}' to spec", file=sys.stderr)
        changed = True

    # Remove
    for pkg in remove_pkgs:
        if pkg not in dep_names:
            print(f"Dependency {pkg} not found", file=sys.stderr)
            continue

        i = dep_names.index(pkg)
        pkg = spec_data["dependencies"].pop(i)
        dep_names.pop(i)
        print(f"Removed '{pkg}' from spec", file=sys.stderr)
        changed = True

    # Update
    if changed:
        save_spec_file()
        env_lock()
        if env_prefix().exists():
            env_install(lazy=True)
