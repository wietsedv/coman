import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import ruamel.yaml as yaml

ENV_HASH_PATTERN = re.compile(r"^# env_hash: (.*)$")
PLATFORM_PATTERN = re.compile(r"^# platform: (.*)$")


def spec_file():
    return Path("environment.yml")


def spec_platforms() -> Optional[List[str]]:
    if not spec_file().exists():
        return []

    with open(spec_file()) as f:
        env = yaml.safe_load(f)
    return env.get("platforms", None)


def lock_file(platform: str):
    return Path(f"conda-{platform}.lock")


def lock_env_hash(lock_path: Path) -> str:
    with open(lock_path) as f:
        for line in f:
            m = ENV_HASH_PATTERN.search(line)
            if m:
                return m.group(1)
        raise RuntimeError("Cannot find env_hash in lockfile")


def edit_spec_file() -> Tuple[dict, Callable]:
    spec_path = spec_file()
    if not spec_file().exists():
        raise FileNotFoundError(f"{spec_path} not found")

    yaml_ = yaml.YAML()
    with open(spec_path) as f:
        spec_data = yaml_.load(f)

    if "channels" not in spec_data:
        spec_data["channels"] = ["conda-forge"]
    if "dependencies" not in spec_data:
        spec_data["dependencies"] = ["python"]

    def save_func():
        with open(spec_path, "w") as f:
            yaml_.dump(spec_data, f)

    return spec_data, save_func
