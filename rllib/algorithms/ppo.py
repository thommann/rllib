"""Proximal Policy Optimization algorithm."""

import torch
import torch.distributions

from rllib.dataset.datatypes import Loss
from rllib.util.neural_networks import resume_learning
from rllib.util.parameter_decay import Constant, ParameterDecay
from rllib.util.value_estimation import discount_cumsum

from .gaac import GAAC


class PPO(GAAC):
    """Proximal Policy Optimization algorithm..

    The PPO algorithm returns a loss that is a combination of three losses.

    - The clipped surrogate objective (Eq. 7).
    - The value function error (Eq. 9).
    - A policy entropy bonus.

    Parameters
    ----------
    policy : AbstractPolicy
    value_function : AbstractValueFunction
    criterion: Type[_Loss], optional.
        Criterion for value function.
    epsilon: float, optional (default=0.2)
        The clipping parameter.
    monte_carlo_target: bool, optional. (default=False).
        Whether to calculate the value targets using MC estimation or adv + value.
    clamp_value: bool, optional. (default=False).
        Whether to clamp the value estimate before computing the value loss.
    lambda_: float, optional. (default=0.97).
        Parameter for Advantage estimation.
    gamma: float, optional. (default=1).
        Discount factor.

    References
    ----------
    Schulman, J., Wolski, F., Dhariwal, P., Radford, A., & Klimov, O. (2017).
    Proximal policy optimization algorithms. ArXiv.

    Engstrom, L., et al. (2020).
    Implementation Matters in Deep Policy Gradients: A Case Study on PPO and TRPO. ICLR.
    """

    def __init__(
        self,
        epsilon=0.2,
        clamp_value=False,
        monte_carlo_target=False,
        standardize_returns=True,
        *args,
        **kwargs,
    ):
        super().__init__(standardize_returns=standardize_returns, *args, **kwargs)

        if not isinstance(epsilon, ParameterDecay):
            epsilon = Constant(epsilon)
        self.epsilon = epsilon

        self.clamp_value = clamp_value
        self.monte_carlo_target = monte_carlo_target

    def reset(self):
        """Reset the optimization (kl divergence) for the next epoch."""
        super().reset()
        # Resume learning after early stopping.
        resume_learning(self.policy)

    def actor_loss(self, trajectory):
        """Get actor loss."""
        state, action, reward, next_state, done, *r = trajectory
        log_p, log_p_old, _, _, entropy = self.get_log_p_kl_entropy(state, action)

        with torch.no_grad():
            adv = self.returns(trajectory)
            if self.standardize_returns:
                adv = (adv - adv.mean()) / (adv.std() + self.eps)

        # Compute surrogate loss.
        ratio = torch.exp(log_p - log_p_old)
        clip_adv = ratio.clamp(1 - self.epsilon(), 1 + self.epsilon()) * adv
        surrogate_loss = -torch.min(ratio * adv, clip_adv).mean()

        return Loss(
            policy_loss=surrogate_loss,
            regularization_loss=-self.entropy_regularization * entropy,
        )

    def get_value_target(self, observation):
        """Get the Q-Target."""
        if self.ope is not None:
            return self.ope(observation)
        elif self.monte_carlo_target:
            final_state = observation.next_state[-1:]
            reward_to_go = self.critic_target(final_state) * (
                1.0 - observation.done[-1:]
            )
            value_target = discount_cumsum(
                torch.cat((observation.reward, reward_to_go)), gamma=self.gamma
            )[:-1]
            return (value_target - value_target.mean()) / (
                value_target.std() + self.eps
            )
        else:
            adv = self.returns(observation)
            if self.standardize_returns:
                adv = (adv - adv.mean()) / (adv.std() + self.eps)

            return adv + self.critic_target(observation.state)

    def process_value_prediction(self, value_prediction, observation):
        """Clamp predicted value."""
        if self.clamp_value:
            old_value_pred = self.critic_target(observation.state).detach()
            value_prediction = torch.max(
                torch.min(value_prediction, old_value_pred + self.epsilon()),
                old_value_pred - self.epsilon(),
            )
        return value_prediction
