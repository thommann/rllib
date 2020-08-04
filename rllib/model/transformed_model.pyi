from typing import Any, List, Tuple, Union

import torch.nn as nn
from torch import Tensor

from rllib.dataset.datatypes import TupleDistribution

from .abstract_model import AbstractModel

class TransformedModel(AbstractModel):

    base_model: AbstractModel
    forward_transformations: nn.ModuleList
    reverse_transformations: nn.ModuleList
    def __init__(
        self,
        base_model: AbstractModel,
        transformations: Union[List[nn.Module], nn.ModuleList],
    ) -> None: ...
    def set_prediction_strategy(self, val: str) -> None: ...
    def forward(self, *args: Tensor, **kwargs: Any) -> TupleDistribution: ...
    def scale(self, state: Tensor, action: Tensor) -> Tensor: ...
    def next_state(self, state: Tensor, action: Tensor) -> TupleDistribution: ...