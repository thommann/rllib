from typing import Any, Type, Union

from torch.nn.modules.loss import _Loss

from rllib.algorithms.sac import SoftActorCritic
from rllib.policy import AbstractPolicy
from rllib.util.parameter_decay import ParameterDecay
from rllib.value_function import AbstractQFunction

from .off_policy_agent import OffPolicyAgent

class SACAgent(OffPolicyAgent):
    algorithm: SoftActorCritic
    def __init__(
        self,
        q_function: AbstractQFunction,
        policy: AbstractPolicy,
        criterion: Type[_Loss],
        eta: Union[float, ParameterDecay],
        regularization: bool = ...,
        *args: Any,
        **kwargs: Any,
    ) -> None: ...
