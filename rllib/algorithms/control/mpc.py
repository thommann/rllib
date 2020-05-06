"""MPC Algorithms."""
from abc import ABCMeta, abstractmethod

import torch
import torch.nn as nn
from torch.distributions import MultivariateNormal

from rllib.util.neural_networks.utilities import repeat_along_dimension
from rllib.util.rollout import rollout_actions
from rllib.dataset.utilities import stack_list_of_tuples
from rllib.util import discount_sum, sample_mean_and_cov
from rllib.util.parameter_decay import ParameterDecay, Constant


def _eval_mpc(dynamical_model, reward_model, horizon, state, gamma,
              action_sequence, termination=None, terminal_reward=None):
    trajectory = rollout_actions(dynamical_model, reward_model, action_sequence, state,
                                 termination)

    trajectory = stack_list_of_tuples(trajectory)
    returns = discount_sum(trajectory.reward, gamma)

    if terminal_reward:
        final_state = trajectory.next_state[-1]
        returns = returns + gamma ** horizon * terminal_reward(final_state)
    return returns


class MPCSolver(nn.Module, metaclass=ABCMeta):
    r"""Solve the discrete time trajectory optimization controller.

    ..math :: u[0:H-1] = \arg \max \sum_{t=0}^{H-1} r(x0, u) + final_reward(x_H)

    When called, it will return the sequence of actions that solves the problem.

    Parameters
    ----------
    dynamical_model: state transition model.
    reward_model: reward model.
    horizon: int.
        Horizon to solve planning problem.
    gamma: float, optional.
        Discount factor.
    scale: float, optional.
        Scale of covariance matrix to sample.
    num_iter: int, optional.
        Number of iterations of solver method.
    num_samples: int, optional.
        Number of samples for shooting method.
    termination: Callable, optional.
        Termination condition.
    terminal_reward: terminal reward model, optional.
    warm_start: bool, optional.
        Whether or not to start the optimization with a warm start.
    default_action: str, optional.
         Default action behavior.

    """

    def __init__(self, dynamical_model, reward_model, horizon, gamma=1.,
                 num_iter=1, num_samples=None, termination=None, scale=0.3,
                 terminal_reward=None, warm_start=False, default_action='zero'):
        super().__init__()
        self.dynamical_model = dynamical_model
        self.reward_model = reward_model
        self.horizon = horizon
        self.gamma = gamma

        self.num_iter = num_iter
        self.num_samples = 10 * horizon if not num_samples else num_samples
        self.termination = termination
        self.terminal_reward = terminal_reward
        self.warm_start = warm_start
        self.default_action = default_action

        self.mean = None
        self._scale = scale
        self.covariance = (scale ** 2) * torch.eye(
            self.dynamical_model.dim_action).repeat(self.horizon, 1, 1)

    def evaluate_action_sequence(self, action_sequence, state):
        """Evaluate action sequence by performing a rollout."""
        trajectory = rollout_actions(
            self.dynamical_model, self.reward_model, action_sequence, state,
            self.termination)

        trajectory = stack_list_of_tuples(trajectory)
        returns = discount_sum(trajectory.reward, self.gamma)

        if self.terminal_reward:
            terminal_reward = self.terminal_reward(trajectory.next_state[-1])
            returns = returns + self.gamma ** self.horizon * terminal_reward
        return returns

    @abstractmethod
    def get_candidate_action_sequence(self):
        """Get candidate actions."""
        raise NotImplementedError

    @abstractmethod
    def get_best_action(self, action_sequence, returns):
        """Get best action."""
        raise NotImplementedError

    @abstractmethod
    def update_sequence_generation(self, elite_actions):
        """Update sequence generation."""
        raise NotImplementedError

    def initialize_actions(self, batch_shape):
        """Initialize mean and covariance of action distribution."""
        if self.warm_start and self.mean is not None:
            next_mean = self.mean[..., 1:, :]
            if self.default_action == 'zero':
                final_action = torch.zeros_like(self.mean[..., :1, :])
            elif self.default_action == 'constant':
                final_action = self.mean[..., -1:, :]
            elif self.default_action == 'mean':
                final_action = torch.mean(next_mean, dim=-2, keepdim=True)
            else:
                raise NotImplementedError
            self.mean = torch.cat((next_mean, final_action), dim=-2)
        else:
            self.mean = torch.zeros(self.horizon, self.dynamical_model.dim_action)
        self.covariance = (self._scale ** 2) * torch.eye(
            self.dynamical_model.dim_action).repeat(self.horizon, 1, 1)

        if self.mean.shape[:-2] != batch_shape:
            self.mean = self.mean.repeat(*batch_shape, 1, 1)
        if self.covariance.shape[:-3] != batch_shape:
            self.covariance = self.covariance.repeat(*batch_shape, 1, 1, 1)

    def forward(self, state):
        """Return action that solves the MPC problem."""
        batch_shape = state.shape[:-1]
        self.initialize_actions(batch_shape)

        state = repeat_along_dimension(state, number=self.num_samples, dim=-2)

        for i in range(self.num_iter):
            action_sequence = self.get_candidate_action_sequence()
            returns = self.evaluate_action_sequence(action_sequence, state)
            elite_actions = self.get_best_action(action_sequence, returns)
            self.update_sequence_generation(elite_actions)

        return self.mean

    def reset(self, warm_action=None):
        """Reset warm action."""
        self.mean = warm_action


class CEMShooting(MPCSolver):
    r"""Cross Entropy Method solves the MPC problem by adaptively sampling.

    The sampling distribution is adapted by fitting a Multivariate Gaussian to the
    best `num_elites' samples (action sequences) for `num_iter' times.

    Parameters
    ----------
    dynamical_model: state transition model.
    reward_model: reward model.
    horizon: int.
        Horizon to solve planning problem.
    gamma: float, optional.
        Discount factor.
    num_iter: int, optional.
        Number of iterations of CEM method.
    num_samples: int, optional.
        Number of samples for shooting method.
    num_elites: int, optional.
        Number of elite samples to keep between iterations.
    termination: Callable, optional.
        Termination condition.
    terminal_reward: terminal reward model, optional.
    warm_start: bool, optional.
        Whether or not to start the optimization with a warm start.
    default_action: str, optional.
         Default action behavior.

    References
    ----------
    Chua, K., Calandra, R., McAllister, R., & Levine, S. (2018).
    Deep reinforcement learning in a handful of trials using probabilistic dynamics
    models. NeuRIPS.

    Botev, Z. I., Kroese, D. P., Rubinstein, R. Y., & L’Ecuyer, P. (2013).
    The cross-entropy method for optimization. In Handbook of statistics
    """

    def __init__(self, dynamical_model, reward_model, horizon, gamma=1., scale=0.3,
                 num_iter=5, num_samples=None, num_elites=None, termination=None,
                 terminal_reward=None, warm_start=False,
                 default_action='zero'):
        super().__init__(
            dynamical_model, reward_model, horizon, gamma=gamma, scale=scale,
            num_iter=num_iter, num_samples=num_samples,
            termination=termination, terminal_reward=terminal_reward,
            warm_start=warm_start, default_action=default_action)
        self.num_elites = max(1, num_samples // 10) if not num_elites else num_elites

    def get_candidate_action_sequence(self):
        """Get candidate actions by sampling from a multivariate normal."""
        action_distribution = MultivariateNormal(self.mean, self.covariance)
        action_sequence = action_distribution.sample((self.num_samples,))
        return action_sequence.transpose(0, -2)

    def get_best_action(self, action_sequence, returns):
        """Get best action by averaging the num_elites samples."""
        idx = torch.topk(returns, k=self.num_elites, largest=True, dim=-1)[1]
        idx = idx.unsqueeze(0).unsqueeze(-1)  # Expand dims to action_sequence.
        idx = idx.repeat_interleave(
            self.horizon, 0).repeat_interleave(self.dynamical_model.dim_action, 1)
        return torch.gather(action_sequence, -2, idx)

    def update_sequence_generation(self, elite_actions):
        """Update distribution by the empirical mean and covariance of best actions."""
        mean, covariance = sample_mean_and_cov(elite_actions.transpose(-1, -2))
        self.mean, self.covariance = mean.transpose(0, -2), covariance.transpose(0, -3)


class RandomShooting(CEMShooting):
    r"""Random Shooting solves the MPC problem by random sampling.

    The sampling distribution is a Multivariate Gaussian and the average of the best
    `num_elites' samples (action sequences) is returned.

    In practice, this is just is the first step of the Cross Entropy Method (n_iter=1).

    Parameters
    ----------
    dynamical_model: state transition model.
    reward_model: reward model.
    horizon: int.
        Horizon to solve planning problem.
    gamma: float, optional.
        Discount factor.
    num_samples: int, optional.
        Number of samples for shooting method.
    num_elites: int, optional.
        Number of elite samples to keep between iterations.
    termination: Callable, optional.
        Termination condition.
    terminal_reward: terminal reward model, optional.
    warm_start: bool, optional.
        Whether or not to start the optimization with a warm start.
    default_action: str, optional.
         Default action behavior.

    References
    ----------
    Nagabandi, A., Kahn, G., Fearing, R. S., & Levine, S. (2018).
    Neural network dynamics for model-based deep reinforcement learning with model-free
    fine-tuning. ICRA.

    Richards, A. G. (2005).
    Robust constrained model predictive control. Ph. D.

    Rao, A. V. (2009).
    A survey of numerical methods for optimal control.
    Advances in the Astronautical Sciences.

    """

    def __init__(self, dynamical_model, reward_model, horizon, gamma=1, scale=0.3,
                 num_samples=None, num_elites=None, termination=None,
                 terminal_reward=None, warm_start=False,
                 default_action='zero'):
        super().__init__(
            dynamical_model, reward_model, horizon, gamma=gamma, scale=scale,
            num_iter=1, num_elites=num_elites, num_samples=num_samples,
            termination=termination, terminal_reward=terminal_reward,
            warm_start=warm_start, default_action=default_action)


class MPPIShooting(MPCSolver):
    """Solve MPC using Model Predictive Path Integral control.

    References
    ----------
    Williams, G., Drews, P., Goldfain, B., Rehg, J. M., & Theodorou, E. A. (2016).
    Aggressive driving with model predictive path integral control. ICRA.

    Williams, G., Aldrich, A., & Theodorou, E. (2015).
    Model predictive path integral control using covariance variable importance
    sampling. arXiv.

    Nagabandi, A., Konoglie, K., Levine, S., & Kumar, V. (2019).
    Deep Dynamics Models for Learning Dexterous Manipulation. arXiv.

    """

    def __init__(self, dynamical_model, reward_model, horizon, gamma=1., scale=.3,
                 num_iter=1, kappa=1., filter_coefficients=[1.], num_samples=None,
                 termination=None, terminal_reward=None, warm_start=False,
                 default_action='zero'):
        super().__init__(
            dynamical_model, reward_model, horizon, gamma=gamma, scale=scale,
            num_iter=num_iter, num_samples=num_samples,
            termination=termination, terminal_reward=terminal_reward,
            warm_start=warm_start, default_action=default_action)

        if not isinstance(kappa, ParameterDecay):
            kappa = Constant(kappa)
        self.kappa = kappa
        self.filter_coefficients = torch.tensor(filter_coefficients)
        self.filter_coefficients /= torch.sum(self.filter_coefficients)

    def get_candidate_action_sequence(self):
        """Get candidate actions by sampling from a multivariate normal."""
        noise_dist = MultivariateNormal(torch.zeros_like(self.mean), self.covariance)
        noise = noise_dist.sample((self.num_samples,))

        lag = len(self.filter_coefficients)
        for i in range(self.horizon):
            weights = self.filter_coefficients[:min(i + 1, lag)]
            aux = torch.einsum('i, ...ij-> ...j', weights.flip(0),
                               noise[..., max(0, i - lag + 1):i + 1, :])
            noise[..., i, :] = aux / torch.sum(weights)

        action_sequence = self.mean + noise
        return action_sequence.transpose(0, -2)

    def get_best_action(self, action_sequence, returns):
        """Get best action by a weighted average of e^kappa returns."""
        returns = self.kappa() * returns
        weights = torch.exp(returns - torch.max(returns))
        weighted_sum = torch.einsum('i,...ij->...j', weights, action_sequence)
        return weighted_sum / torch.sum(weights)

    def update_sequence_generation(self, elite_actions):
        """Update distribution by the fitting the elite_actions to the mean."""
        self.mean = elite_actions
        self.kappa.update()
