from typing import Any, Type, Union

from torch.nn.modules.loss import _Loss

from rllib.algorithms.dpg import DPG
from rllib.policy import AbstractPolicy
from rllib.util.parameter_decay import ParameterDecay
from rllib.value_function import AbstractQFunction

from .off_policy_agent import OffPolicyAgent

class DPGAgent(OffPolicyAgent):
    algorithm: DPG
    def __init__(
        self,
        q_function: AbstractQFunction,
        policy: AbstractPolicy,
        criterion: Type[_Loss],
        exploration_noise: Union[float, ParameterDecay],
        policy_noise: float = ...,
        noise_clip: float = ...,
        *args: Any,
        **kwargs: Any,
    ) -> None: ...
