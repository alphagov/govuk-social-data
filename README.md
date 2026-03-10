### README 

## To run unit tests

python -m pytest tests/test_main.py -v 2>&1

## Setting up the environment (Poetry-only approach)

### Prerequisites
- [pyenv](https://github.com/pyenv/pyenv) installed
- [Poetry](https://python-poetry.org/) installed via pipx:
```bash
pipx install poetry
```

### From scratch
```bash
# 1. Install Python via pyenv and set it locally
pyenv install 3.14.3
pyenv local 3.14.3

# 2. Initialise Poetry in the project (skip if pyproject.toml already exists)
poetry init

# 3. Configure Poetry to create the .venv inside the project directory
poetry config virtualenvs.in-project true

# 4. Create the virtualenv using the pyenv-managed Python
poetry env use $(pyenv which python)

# 5. Install dependencies
poetry install
```

### If you already have an existing pyenv virtualenv
```bash
# 1. Create the named virtualenv (if not already created)
pyenv install 3.14.3
pyenv virtualenv 3.14.3 <your-environment-name>

# 2. Activate it (pyenv reads .python-version automatically)
pyenv local <your-environment-name>

# 3. Wire it up with Poetry
poetry env use $(pyenv which python)

# 4. Install dependencies
poetry install
```

### Useful commands
```bash
# Check which environment Poetry is using
poetry env info --path

# Point Poetry at a specific Python path
poetry env use /full/path/to/python

# Activate the Poetry shell
poetry shell

# Run a command inside the environment
poetry run python your_script.py
```