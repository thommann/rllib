from typing import Callable, List, Tuple, Union

from numpy import ndarray
from torch import Tensor

from rllib.agent import AbstractAgent
from rllib.dataset.datatypes import (
    Action,
    Distribution,
    Observation,
    State,
    Termination,
    Trajectory,
)
from rllib.environment import AbstractEnvironment
from rllib.model import AbstractModel
from rllib.policy import AbstractPolicy
from rllib.reward import AbstractReward

def step_env(
    environment: AbstractEnvironment,
    state: Union[int, ndarray],
    action: Union[int, ndarray],
    pi: Distribution = None,
    render: bool = False,
    goal: Union[int, ndarray] = None,
) -> Tuple[Observation, Union[int, ndarray], bool, dict]: ...
def step_model(
    dynamical_model: AbstractModel,
    reward_model: AbstractReward,
    termination: Termination,
    state: Tensor,
    action: Tensor,
    done: Tensor,
    pi: Distribution = None,
) -> Tuple[Observation, Tensor, Tensor]: ...
def rollout_agent(
    environment: AbstractEnvironment,
    agent: AbstractAgent,
    num_episodes: int = 1,
    max_steps: int = 1000,
    render: bool = False,
    print_frequency: int = 0,
    plot_frequency: int = 0,
    save_milestones: List[int] = None,
    plot_callbacks: List[Callable[[AbstractAgent, int], None]] = None,
) -> None: ...
def rollout_policy(
    environment: AbstractEnvironment,
    policy: AbstractPolicy,
    num_episodes: int = 1,
    max_steps: int = 1000,
    render: bool = False,
    **kwargs,
) -> List[Trajectory]: ...
def rollout_model(
    dynamical_model: AbstractModel,
    reward_model: AbstractReward,
    policy: AbstractPolicy,
    initial_state: State,
    termination: Termination = None,
    max_steps: int = 1000,
    **kwargs,
) -> Trajectory: ...
def rollout_actions(
    dynamical_model: AbstractModel,
    reward_model: AbstractReward,
    action_sequence: Action,
    initial_state: State,
    termination: Termination = None,
) -> Trajectory: ...
