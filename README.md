### README 

## To run unit tests

python -m pytest tests/test_main.py -v 2>&1


## installing poetry
poetry init 
poetry config virtualenvs.in-project true

# check current path 
poetry env info --path

# set full path 
poetry env use /full/path/to/python