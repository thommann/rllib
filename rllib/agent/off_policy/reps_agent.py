"""Implementation of REPS Agent."""

import torch
from torch.optim import Adam

from rllib.algorithms.reps import REPS
from rllib.dataset.experience_replay import ExperienceReplay
from rllib.policy import NNPolicy
from rllib.util.neural_networks.utilities import deep_copy_module
from rllib.value_function import NNValueFunction

from .off_policy_agent import OffPolicyAgent


class REPSAgent(OffPolicyAgent):
    """Implementation of the REPS algorithm.

    References
    ----------
    Peters, J., Mulling, K., & Altun, Y. (2010, July).
    Relative entropy policy search. AAAI.

    Deisenroth, M. P., Neumann, G., & Peters, J. (2013).
    A survey on policy search for robotics. Foundations and Trends® in Robotics.
    """

    def __init__(
        self,
        policy,
        value_function,
        optimizer,
        memory,
        epsilon,
        regularization=False,
        *args,
        **kwargs,
    ):
        super().__init__(memory=memory, optimizer=optimizer, *args, **kwargs)

        self.algorithm = REPS(
            policy=policy,
            critic=value_function,
            epsilon=epsilon,
            regularization=regularization,
            gamma=self.gamma,
        )
        # Over-write optimizer.
        self.optimizer = type(optimizer)(
            [
                p
                for name, p in self.algorithm.named_parameters()
                if "target" not in name
            ],
            **optimizer.defaults,
        )

        self.policy = self.algorithm.policy

    def observe(self, observation):
        """See `AbstractAgent.observe'."""
        super().observe(observation)
        self.memory.append(observation)

    def end_episode(self):
        """See `AbstractAgent.end_episode'."""
        if (self.total_episodes + 1) % self.num_rollouts == 0 and self._training:
            self.learn()

        super().end_episode()

    def learn(self):
        """See `AbstractAgent.train_agent'."""
        old_policy = deep_copy_module(self.policy)
        self._optimizer_dual()

        self.policy.prior = old_policy
        self._fit_policy()

        self.memory.reset()  # Erase memory.
        self.algorithm.update()  # Step the etas in REPS.

    def _optimizer_dual(self):
        """Optimize the dual function."""
        self._optimize_loss(self.num_iter, loss_name="dual_loss")

    def _fit_policy(self):
        """Fit the policy optimizing the weighted negative log-likelihood."""
        self._optimize_loss(self.num_iter, loss_name="policy_loss")

    def _optimize_loss(self, num_iter, loss_name="dual_loss"):
        """Optimize the loss performing `num_iter' gradient steps."""
        for _ in range(num_iter):
            observation, idx, weight = self.memory.sample_batch(self.batch_size)

            def closure(obs=observation):
                """Gradient calculation."""
                self.optimizer.zero_grad()
                losses_ = self.algorithm(obs)
                self.optimizer.zero_grad()
                loss_ = getattr(losses_, loss_name)
                loss_.backward()
                torch.nn.utils.clip_grad_norm_(
                    self.algorithm.parameters(), self.clip_gradient_val
                )

                return loss_

            losses = self.optimizer.step(closure=closure).item()
            self.logger.update(**{loss_name: losses})

            self.counters["train_steps"] += 1
            if self.early_stop(losses, **self.algorithm.info()):
                break

    @classmethod
    def default(cls, environment, *args, **kwargs):
        """See `AbstractAgent.default'."""
        value_function = NNValueFunction(
            dim_state=environment.dim_state,
            num_states=environment.num_states,
            layers=[200, 200],
            biased_head=True,
            non_linearity="Tanh",
            tau=5e-3,
            input_transform=None,
        )
        policy = NNPolicy(
            dim_state=environment.dim_state,
            dim_action=environment.dim_action,
            num_states=environment.num_states,
            num_actions=environment.num_actions,
            layers=[200, 200],
            biased_head=True,
            non_linearity="Tanh",
            tau=5e-3,
            input_transform=None,
            deterministic=False,
        )

        optimizer = Adam(value_function.parameters(), lr=3e-4)
        memory = ExperienceReplay(max_len=50000, num_steps=0)

        return cls(
            policy=policy,
            value_function=value_function,
            optimizer=optimizer,
            memory=memory,
            epsilon=1.0,
            regularization=False,
            num_iter=5 if kwargs.get("test", False) else 200,
            batch_size=100,
            train_frequency=0,
            num_rollouts=15,
            comment=environment.name,
            *args,
            **kwargs,
        )
