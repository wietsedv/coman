# Coma: Conda Manager

A simple CLI for Conda dependency management. Coma gives you simple commands to manage dependencies in your `environment.yml` and properly uses lockfiles. Moreover, it manages unique Conda environments per project directory. Coma mimicks the workflow of [Poetry](https://github.com/python-poetry/poetry) (or any other modern dependency manager), but then for Conda.

**Warning:** Coma is work in progress and may not work in every scenario. 

Coma is expected to work fully on Linux and macOS if a recent version of mamba is already available on your system. Otherwise you can currently only install (`coma install`) and activate (`coma shell`) environments. In other words: you can use Coma on a production system without any external dependencies.

Coma manages:

 - `environment.yml`:
   - `add`/`remove` dependencies with version constraints
 - `conda-{platform}.lock`:
   - (multi-platform) lock files for reproducable environments
 - `{envs_dir}/{basename}-{hash}`:
   - unique environments for your project in your default environments directory

## Installation
There is currently no Coma conda recipe. Coma is designed to work independently of Conda, so that it can install and run Conda environments on any system (with micromamba).

```bash
pip install -U coma

# pipx (recommended)
# Make sure that pipx uses the python environment in which conda/mamba are installed (the conda base environment) if you want to be able to edit your environment.yml and lock files.
conda activate base
pipx install -e --python python coma
```

### Latest development version
```
pip install --user --upgrade git+https://github.com/wietsedv/coma.git#egg=coma
```

### Development
```bash
git clone git@github.com:wietsedv/coma.git
cd coma && pip install -e .
```

## Quick start
```bash
cd projects/MyProject

# show system and environment status
coma info

# create environment.yml and lock file
coma init

# install the environment
coma install

# add "requests" dependency to environment.yml, the lock file(s) and your installed environment
coma add requests

# show the installed packages
coma show

# run a command (use -- to avoid conflicts between coma and the command you run)
coma run -- python --version

# activate your environment in your current shell (at least works with bash and zsh)
eval $(coma shell)
```
