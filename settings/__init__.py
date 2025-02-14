from types import SimpleNamespace
from json import load
from pathlib import Path

from sys import path
path.append('.')

class Settings(SimpleNamespace):
    """
    Interface to the settings file
    """

    FILENAME = '../settings.json' # path relative to the scripts/ folder

    def open_as_dict() -> dict():
        settings = dict()
        with open(Settings.FILENAME) as settings_file:
            settings = load(settings_file)
        return settings
    
    def path(name : str) -> Path:
        # open settings as dict, get selected entry in 'paths', convert to Path, expand '~' to user home
        return Path.expanduser(Path(Settings.open_as_dict()['paths'][name])).absolute()