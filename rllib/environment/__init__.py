"""Import environments."""
try:
    import rllib.environment.mujoco
except Exception:  # Mujoco not installed.
    pass
import rllib.environment.mdps
import rllib.environment.system_environment
import rllib.environment.vectorized

from .abstract_environment import AbstractEnvironment
from .gym_environment import *
from .mdp import *
from .system_environment import *
from .utilities import *
