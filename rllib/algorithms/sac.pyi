"""Soft Actor-Critic Algorithm."""
from typing import Optional, Tuple, Union

import torch.nn as nn
from torch import Tensor
from torch.nn.modules.loss import _Loss

from rllib.dataset.datatypes import Observation, Termination
from rllib.model import AbstractModel
from rllib.policy import AbstractPolicy
from rllib.reward import AbstractReward
from rllib.util.parameter_decay import ParameterDecay
from rllib.util.utilities import RewardTransformer
from rllib.value_function import AbstractQFunction

from .abstract_algorithm import AbstractAlgorithm, SACLoss, TDLoss

class SoftActorCritic(AbstractAlgorithm):
    q_function: AbstractQFunction
    q_target: AbstractQFunction
    policy: AbstractPolicy
    criterion: _Loss
    gamma: float
    reward_transformer: RewardTransformer
    eta: ParameterDecay
    target_entropy: Union[float, Tensor]
    dist_params: dict
    def __init__(
        self,
        policy: AbstractPolicy,
        q_function: AbstractQFunction,
        criterion: _Loss,
        gamma: float,
        eta: Union[ParameterDecay, float] = ...,
        regularization: bool = ...,
        reward_transformer: RewardTransformer = RewardTransformer(),
    ) -> None: ...
    def get_q_target(
        self, reward: Tensor, next_state: Tensor, done: Tensor
    ) -> Tensor: ...
    def actor_loss(self, state: Tensor) -> Tuple[Tensor, Tensor]: ...
    def critic_loss(
        self, state: Tensor, action: Tensor, q_target: Tensor
    ) -> TDLoss: ...
    def forward(self, observation: Observation, **kwargs) -> SACLoss: ...

class MBSoftActorCritic(SoftActorCritic):
    """Model Based Soft-Actor Critic."""

    dynamical_model: AbstractModel
    reward_model: AbstractReward
    termination: Termination

    num_steps: int
    num_samples: int
    def __init__(
        self,
        policy: AbstractPolicy,
        q_function: AbstractQFunction,
        dynamical_model: AbstractModel,
        reward_model: AbstractReward,
        criterion: _Loss,
        gamma: float,
        eta: Union[ParameterDecay, float] = ...,
        regularization: bool = ...,
        reward_transformer: RewardTransformer = ...,
        termination: Optional[Termination] = ...,
        num_steps: int = ...,
        num_samples: int = ...,
    ) -> None: ...
