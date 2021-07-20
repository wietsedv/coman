from pkg_resources import get_distribution

from .poco import cli

__version__ = get_distribution(__name__).version
