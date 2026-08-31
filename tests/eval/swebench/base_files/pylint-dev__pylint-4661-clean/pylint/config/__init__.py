


































import pickle
import sys

from pylint.config.configuration_mixin import ConfigurationMixIn
from pylint.config.find_default_config_files import find_default_config_files
from pylint.config.man_help_formatter import _ManHelpFormatter





















elif USER_HOME == "~":
    PYLINT_HOME = ".pylint.d"
else:
    PYLINT_HOME = os.path.join(USER_HOME, ".pylint.d")


def _get_pdata_path(base_name, recurs):
