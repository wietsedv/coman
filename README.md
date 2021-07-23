# CoMan: Conda Manager

[![PyPI](https://img.shields.io/pypi/v/coman)](https://pypi.org/project/coman/)
![PyPI - License](https://img.shields.io/pypi/l/coman)

A simple CLI for Conda dependency management. CoMan gives you simple commands to manage dependencies in your `environment.yml` and properly uses lockfiles. Moreover, it manages unique Conda environments per project directory. CoMan mimicks the workflow of [Poetry](https://github.com/python-poetry/poetry) (or any other modern dependency manager), but then for Conda.

**Warning:** CoMan is work in progress and may not work in every scenario. 

CoMan does not require any external dependencies, but it is fastest if you install [Mamba](https://github.com/mamba-org/mamba) (see Caveats).

CoMan manages:

 - `environment.yml`:
   - `add`/`remove` dependencies with version constraints
 - `conda-{platform}.lock`:
   - (multi-platform) lock files for reproducable environments
 - `{envs_dir}/{basename}-{hash}`:
   - unique environments for your project in your default environments directory

## Installation
There is currently no CoMan conda recipe. CoMan is designed to work independently of Conda, so that it can install and run Conda environments on any system (with Micromamba).

```bash
pip install -U coman

# pipx (recommended)
# make sure that pipx uses the python environment in which conda/mamba are installed (the conda base environment) if you have those installed
conda activate base
pipx install -e --python python coman
```

### Latest development version
```
pip install --user --upgrade git+https://github.com/wietsedv/coman.git#egg=coman
```

### Development
```bash
git clone git@github.com:wietsedv/coman.git
cd coman && pip install -e .
```

## Quick start
```bash
cd projects/MyProject

# "cm" is short for "coman"; use the command you prefer

# show system and environment status
cm info

# create environment.yml and lock file
cm init

# install the environment
cm install

# add "requests" dependency to environment.yml, the lock file(s) and your installed environment
cm add requests

# show the installed packages
cm show

# run a command (use -- to avoid conflicts between coman and the command you run)
cm run -- python --version

# activate your environment in your current shell (at least works with bash and zsh)
eval $(cm shell)
```

## Caveats
CoMan uses Mamba and Conda from your system if you have those installed. Otherwise, it will fall back to automatically installed Micromamba and Conda-standalone binaries. This means that you can use CoMan without any external dependencies, but CoMan will be fastest if you install Mamba.

Moreover, you may experience problems with the `coman run` command if you do not have Conda on your system. You should then still be able to use `coman shell`.
