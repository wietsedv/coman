import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from typing import Dict, List, Set, Tuple
import urllib.parse

import click

from coman.system import Conda
from coman.utils import COLORS


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


def conda_env_override(conda: Conda, platform: str) -> Dict[str, str]:
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
        try:
            content = json.loads(out.stdout)
        except json.JSONDecodeError:
            print(out.stdout)
            print(out.stderr)
            click.secho(f"\nCould not determine hash for: {spec}", fg="red", file=sys.stderr)
            exit(1)
        if name in content:
            assert len(content[name]) == 1
            yield content[name][0]
            found.add(name)


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


def solve_specs_for_arch(conda: Conda, lock_spec: LockSpecification) -> dict:
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
    if lock_spec.channels:
        args.append("--override-channels")
    for channel in lock_spec.channels:
        args.extend(["--channel", channel])
    args.extend(lock_spec.specs)

    p = subprocess.run(
        args,
        env=conda_env_override(conda, lock_spec.platform),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf8",
    )

    try:
        res = json.loads(p.stdout)
    except json.decoder.JSONDecodeError:
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


def create_lockfile_from_spec(conda: Conda, lock_spec: LockSpecification) -> List[str]:
    # print("Resolving dependencies", file=sys.stderr)
    dry_run_install = solve_specs_for_arch(conda, lock_spec)

    lockfile_contents = [
        "# Generated by conda-lock.",
        f"# platform: {lock_spec.platform}",
        f"# env_hash: {lock_spec.env_hash()}\n",
        "@EXPLICIT\n",
    ]

    fetch_actions = dry_run_install["actions"]["FETCH"]
    link_actions = dry_run_install["actions"]["LINK"]

    for link in link_actions:
        if conda.is_micromamba():
            link["url_base"] = fn_to_dist_name(link["url"])
            link["dist_name"] = fn_to_dist_name(link["fn"])
            link["platform"] = link["subdir"]
            link["channel"] = urllib.parse.urlsplit(link["channel"]).path.split('/')[-2]
        else:
            link["url_base"] = f"{link['base_url']}/{link['platform']}/{link['dist_name']}"

        link["url"] = f"{link['url_base']}.tar.bz2"
        link["url_conda"] = f"{link['url_base']}.conda"

    # link_dists = {link["dist_name"] for link in link_actions}
    fetch_by_dist_name = {fn_to_dist_name(pkg["fn"]): pkg for pkg in fetch_actions}

    # print("Determining hashes", file=sys.stderr)

    # non_fetch_packages = link_dists - set(fetch_by_dist_name)
    # if len(non_fetch_packages) > 0:
    #     for search_res in search_for_md5s(
    #             conda=conda,
    #             package_specs=[x for x in link_actions if x["dist_name"] in non_fetch_packages],
    #             platform=lock_spec.platform,
    #             channels=lock_spec.channels,
    #     ):
    #         dist_name = fn_to_dist_name(search_res["fn"])
    #         fetch_by_dist_name[dist_name] = search_res
    #         print(f"- {dist_name}", file=sys.stderr)

    for pkg in link_actions:
        dist_name = (fn_to_dist_name(pkg["fn"]) if conda.is_micromamba() else pkg["dist_name"])
        if dist_name in fetch_by_dist_name:
            url = fetch_by_dist_name[dist_name]["url"]
            md5 = fetch_by_dist_name[dist_name]["md5"]
            lockfile_contents.append(f"{url}#{md5}")
        else:
            url = pkg["url"]
            lockfile_contents.append(url)

    def sanitize_lockfile_line(line):
        line = line.strip()
        if line == "":
            return "#"
        else:
            return line

    lockfile_contents = [sanitize_lockfile_line(line) for line in lockfile_contents]

    return lockfile_contents
