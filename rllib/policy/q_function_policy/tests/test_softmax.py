from rllib.policy import SoftMax
from rllib.value_function import NNQFunction
import torch
import torch.testing
import pytest


@pytest.fixture(params=[0.1, 1.0])
def t_start(request):
    return request.param


@pytest.fixture
def q_function():
    return NNQFunction(num_actions=2, dim_action=1, num_states=4, dim_state=1)


def test_discrete(t_start, q_function):
    policy = SoftMax(q_function, start=t_start)
    for t in range(100):
        state = torch.randint(4, ())
        logits = q_function(state)
        probs = torch.softmax(logits / t_start, dim=0)
        print(probs)
        torch.testing.assert_allclose(
            policy(state).probs, probs)