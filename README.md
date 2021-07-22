# Poco: Simpler Conda dependency management

A simple CLI for Conda dependency management. Poco gives you simple commands to manage dependencies in your `environment.yml` and properly uses lockfiles. Moreover, it manages unique Conda environments per project directory. Poco mimicks the workflow of [**Po**etry](https://github.com/python-poetry/poetry) (or any other modern dependency manager), but then for **Co**nda.

**Warning:** Poco is work in progress and may not work in every scenario. 

Poco is expected to work fully on Linux and macOS if a recent version of mamba is already available on your system. Otherwise you can currently only install (`poco install`) and activate (`poco shell`) environments. In other words: you can use Poco on a production system without any external dependencies.

Poco manages:

 - `environment.yml`:
   - `add`/`remove` dependencies with version constraints
 - `conda-{platform}.lock`:
   - (multi-platform) lock files for reproducable environments
 - `{envs_dir}/{basename}-{hash}`:
   - unique environments for your project in your default environments directory

## Installation
There is currently no Poco conda recipe. Poco is designed to work independently of Conda, so that it can install and run Conda environments on any system (with micromamba).

```bash
pip install -U poco

# pipx (recommended)
# Make sure that pipx uses the python environment in which conda/mamba are installed (the conda base environment) if you want to be able to edit your environment.yml and lock files.
conda activate base
pipx install -e --python python poco
```

### Latest development version
```
pip install --user --upgrade git+https://github.com/wietsedv/poco.git#egg=poco
```

### Development
```bash
git clone git@github.com:wietsedv/poco.git
cd poco && pip install -e .
```

## Quick start
```bash
cd projects/MyProject

# show system and environment status
poco info

# create environment.yml and lock file
poco init

# install the environment
poco install

# add "requests" dependency to environment.yml, the lock file(s) and your installed environment
poco add requests

# show the installed packages
poco show

# run a command (use -- to avoid conflicts between poco and the command you run)
poco run -- python --version

# activate your environment in your current shell (at least works with bash and zsh)
eval $(poco shell)
```
