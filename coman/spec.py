import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ENV_HASH_PATTERN = re.compile(r"^# env_hash: (.*)$")
PLATFORM_PATTERN = re.compile(r"^# platform: (.*)$")

PLATFORMS = ["linux-64", "linux-aarch64", "linux-ppc64le", "osx-64", "osx-arm64", "win-64"]


def conda_lock_file(platform: str):
    return Path(f"conda-{platform}.lock")


def pip_lock_file():
    return Path("requirements.txt")


class Specification:
    def __init__(self) -> None:
        self.spec_file = Path("environment.yml")
        self.data = None

        import ruamel.yaml
        self._yaml = ruamel.yaml.YAML()

    def read(self) -> Dict[str, Any]:
        if self.data is None:
            if not self.spec_file.exists():
                print(
                    f"Specification file `{self.spec_file}` is not found in the current directory. Create it with `coman init`",
                    file=sys.stderr)
                exit(1)

            with open(self.spec_file) as f:
                self.data = self._yaml.load(f)

            if "channels" not in self.data:
                self.data["channels"] = ["conda-forge"]
            if "dependencies" not in self.data:
                self.data["dependencies"] = []
        return self.data

    def write(self):
        with open(self.spec_file, "w") as f:
            self._yaml.dump(self.data, f)

    def dependencies(self) -> Tuple[List[str], List[str]]:
        spec_data = self.read()
        conda_names, pip_names = [], []
        for pkg in spec_data["dependencies"]:
            if isinstance(pkg, str):
                conda_names.append(pkg.split(" ")[0])
            elif isinstance(pkg, dict) and "pip" in pkg:
                for pip_pkg in pkg["pip"]:
                    pip_names.append(pip_pkg.split(" ")[0])
        return conda_names, pip_names

    def platforms(self, default: str) -> List[str]:
        spec_data = self.read()
        platform_names = spec_data.get("platforms", [default])
        return platform_names

    def channels(self) -> List[str]:
        spec_data = self.read()
        return spec_data.get("channels", ["conda-forge"])

    def dependency_infos(self) -> Tuple[List[dict], List[dict]]:
        spec_data = self.read()
        conda_comments = spec_data["dependencies"].ca.items
        conda_infos, pip_infos = [], []
        for i, pkg in enumerate(spec_data["dependencies"]):
            if isinstance(pkg, str):
                name, ver = pkg.split()
                comment = conda_comments[i][0].value.strip() if i in conda_comments else ""
                conda_infos.append({"name": name, "version": ver, "comment": comment})
            elif isinstance(pkg, dict) and "pip" in pkg:
                pip_comments = spec_data["dependencies"][i]["pip"].ca.items
                for j, pip_pkg in enumerate(pkg["pip"]):
                    if isinstance(pip_pkg, str):
                        name, ver = pip_pkg.split()
                        comment = pip_comments[j][0].value.strip() if j in pip_comments else ""
                        pip_infos.append({"name": name, "channel": "pypi", "version": ver, "comment": comment})
        return conda_infos, pip_infos

    def platform_infos(self, default: str) -> List[dict]:
        platforms = _yaml_info_list(self.read(), "platforms", "platform") or [{"platform": default}]
        return platforms

    def channel_infos(self) -> List[dict]:
        return _yaml_info_list(self.read(), "channels", "channel") or [{"channel": "conda-forge"}]


def _yaml_info_list(data, key: str, item_key: str) -> Optional[List[dict]]:
    if key not in data:
        return
    comments = data[key].ca.items
    items = []
    for i, item in enumerate(data[key]):
        comment = comments[i][0].value.strip() if i in comments else ""
        items.append({item_key: item, "comment": comment})
    return items


def spec_pip_requirements(spec: Specification):
    spec_data = spec.read()
    for pkg in spec_data["dependencies"]:
        if isinstance(pkg, dict) and "pip" in pkg:
            pip_pkgs = []
            for pkg in pkg["pip"]:
                _, ver = pkg.split(" ")
                if ver.startswith("http"):
                    pip_pkgs.append(ver)
                else:
                    pip_pkgs.append(pkg)
            return "\n".join(pip_pkgs)


def conda_lock_hash(platform: str) -> str:
    with open(conda_lock_file(platform)) as f:
        for line in f:
            m = ENV_HASH_PATTERN.search(line)
            if m:
                return m.group(1)
        raise RuntimeError("Cannot find env_hash in conda lock file")


def pip_lock_hash() -> Optional[str]:
    lock_path = pip_lock_file()
    if not lock_path.exists():
        return None
    with open(lock_path) as f:
        for line in f:
            m = ENV_HASH_PATTERN.search(line)
            if m:
                return m.group(1)
        raise RuntimeError("Cannot find env_hash in pip lock file")


def pip_lock_comments():
    lock_path = pip_lock_file()
    if not lock_path.exists():
        return {}

    comments = {}
    with open(lock_path) as f:
        name, comment = None, ""
        for line in f:
            if name is not None:
                if line.startswith("    --hash="):
                    continue
                if line == "    # via -r -\n":
                    continue
                if line.startswith("    # "):
                    comment += " " + line[6:].strip()
                    continue
                if comment:
                    comments[name] = comment
                name = None
            if name is None:
                if line[0] in "#\n":
                    continue
                if "==" in line:
                    name, comment = line.split("==")[0], ""
                    continue
        if comment:
            comments[name] = comment

    return comments
