"""On Policy Agent."""
from typing import List

from .abstract_agent import AbstractAgent
from rllib.algorithms.abstract_algorithm import AbstractAlgorithm
from rllib.dataset.datatypes import Observation


class OnPolicyAgent(AbstractAgent):
    """Template for an on-policy algorithm."""

    algorithm: AbstractAlgorithm
    num_rollouts: int
    trajectories: List[List[Observation]]

    def __init__(self, env_name: str,
                 num_rollouts: int = 1,
                 gamma: float = 1.0, exploration_steps: int = 0,
                 exploration_episodes: int = 0, comment: str = ''
                 ) -> None: ...