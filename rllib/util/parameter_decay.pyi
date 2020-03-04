from abc import ABCMeta, abstractmethod

class ParameterDecay(object, metaclass=ABCMeta):
    start: float
    end: float
    decay: float
    step: int

    def __init__(self, start: float, end: float = None, decay: float = None
                 ) -> None: ...

    @abstractmethod
    def __call__(self) -> float: ...

    def update(self) -> None: ...


class ExponentialDecay(ParameterDecay):
    def __call__(self) -> float: ...


class LinearDecay(ParameterDecay):
    def __call__(self) -> float: ...