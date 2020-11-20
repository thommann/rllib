"""Implementation of PPO Algorithm."""
from torch.nn.modules import loss

from rllib.algorithms.ppo import PPO
from rllib.util.early_stopping import EarlyStopping
from rllib.util.neural_networks.utilities import stop_learning
from rllib.value_function import NNValueFunction

from .actor_critic_agent import ActorCriticAgent


class PPOAgent(ActorCriticAgent):
    """Implementation of the PPO Agent.

    References
    ----------
    Schulman, J., Wolski, F., Dhariwal, P., Radford, A., & Klimov, O. (2017).
    Proximal policy optimization algorithms. ArXiv.
    """

    def __init__(
        self,
        policy,
        critic,
        epsilon=0.2,
        lambda_=0.95,
        target_kl=0.005,
        entropy_regularization=0.01,
        monte_carlo_target=False,
        clamp_value=True,
        criterion=loss.MSELoss,
        num_iter=80,
        *args,
        **kwargs,
    ):
        super().__init__(
            critic=critic, policy=policy, num_iter=num_iter, *args, **kwargs
        )
        self.algorithm = PPO(
            critic=critic,
            policy=policy,
            epsilon=epsilon,
            criterion=criterion(reduction="mean"),
            entropy_regularization=entropy_regularization,
            monte_carlo_target=monte_carlo_target,
            clamp_value=clamp_value,
            lambda_=lambda_,
            *args,
            **kwargs,
        )

        self.policy = self.algorithm.policy
        self.target_kl = target_kl
        self._early_stopping = EarlyStopping(epsilon=1.5 * target_kl, relative=False)

    def early_stop(self, losses, **kwargs):
        """Early stop the training algorithm."""
        kl = kwargs.get("kl_div", kwargs.get("approx_kl_div", self.target_kl))
        self.early_stopping_algorithm.update(kl)

        if self.early_stopping_algorithm.stop:
            stop_learning(self.policy)
        self.early_stopping_algorithm.reset()

        return False

    @classmethod
    def default(cls, environment, critic=None, *args, **kwargs):
        """See `AbstractAgent.default'."""
        if critic is None:
            critic = NNValueFunction.default(environment)
        return super().default(environment, critic=critic, *args, **kwargs)
