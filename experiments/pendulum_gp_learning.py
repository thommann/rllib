import math

import gpytorch
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torch.distributions import Uniform
import torch.optim as optim
from gpytorch.distributions import Delta
from tqdm import tqdm

from rllib.algorithms.mppo import MBMPPO, train_mppo
from rllib.dataset.dataset import TrajectoryDataset
from rllib.dataset.experience_replay import BootstrapExperienceReplay
from rllib.dataset.datatypes import Observation
from rllib.dataset.transforms import MeanFunction, StateActionNormalizer, ActionClipper
from rllib.dataset.utilities import stack_list_of_tuples, bootstrap_trajectory
from rllib.environment.system_environment import SystemEnvironment
from rllib.environment.systems import InvertedPendulum
from rllib.model.gp_model import ExactGPModel
from rllib.model.pendulum_model import PendulumModel
from rllib.model.derived_model import OptimisticModel, TransformedModel
from rllib.model.ensemble_model import EnsembleModel
from rllib.policy import NNPolicy
from rllib.reward.pendulum_reward import PendulumReward
from rllib.util.collect_data import collect_model_transitions

from rllib.util.utilities import tensor_to_distribution
from rllib.util.plotting import plot_learning_losses, plot_values_and_policy
from rllib.util.rollout import rollout_model, rollout_policy
from rllib.util.training import train_model
from rllib.util.neural_networks import DeterministicEnsemble
from rllib.util.gaussian_processes.mlls import exact_mll
from rllib.util.neural_networks.utilities import freeze_parameters
from rllib.value_function import NNValueFunction

torch.manual_seed(0)
np.random.seed(0)


class StateTransform(nn.Module):
    extra_dim = 1

    def forward(self, states_):
        """Transform state before applying function approximation."""
        angle, angular_velocity = torch.split(states_, 1, dim=-1)
        states_ = torch.cat((torch.cos(angle), torch.sin(angle), angular_velocity),
                            dim=-1)
        return states_

class MeanModel(nn.Module):
    def forward(self, state, action):
        """Pass the mean model."""
        return state

transformations = [
    ActionClipper(-1, 1),
    MeanFunction(MeanModel()),
    # StateActionNormalizer()
]


# %% Collect Data.
num_data = 200
reward_model = PendulumReward()
dynamic_model = PendulumModel(mass=0.3, length=0.5, friction=0.005)

transitions = collect_model_transitions(
    Uniform(torch.tensor([-2 * np.pi, -12]), torch.tensor([2 * np.pi, 12])),
    Uniform(torch.tensor([-1.]), torch.tensor([1.])),
    dynamic_model, reward_model, num_data)

# %% Bootstrap into different trajectories.
dataset = BootstrapExperienceReplay(max_len=int(1e4), transformations=transformations,
                                    num_bootstraps=1)
for transition in transitions:
    dataset.append(transition)

data = dataset.all_data
split = 50
dataloader = DataLoader(dataset, batch_size=64, shuffle=True)

# %% Train a Model
model = ExactGPModel(data.state[:split, 0], data.action[:split, 0],
                     data.next_state[:split, 0],
                     input_transform=StateTransform(), max_num_points=75)

model.eval()
mean, stddev = model(torch.randn(8, 5, 2), torch.randn(8, 5, 1))
# print(mean.shape, stddev.shape, mean, stddev)

model.train()
out = model(data.state[:split, 0], data.action[:split, 0])
optimizer = torch.optim.Adam([
    {'params': model.parameters()},  # Includes GaussianLikelihood parameters
], lr=0.1)

for i in range(100):
    optimizer.zero_grad()
    output = tensor_to_distribution(model(data.state[:split, 0], data.action[:split, 0]))

    with gpytorch.settings.fast_pred_var():
        loss = exact_mll(output, data.next_state[:split, 0].T, model.gp)
    loss.backward()

    optimizer.step()
    print('Iter %d/%d - Loss: %.3f' % (i + 1, 30, loss.item()))

print([(n, p) for (n, p) in model.named_parameters()])
print([(gp.output_scale, gp.length_scale) for gp in model.gp])

model.add_data(data.state[split:2 * split, 0], data.action[split:2 * split, 0],
               data.next_state[split:2 * split, 0])

for i in range(70):
    optimizer.zero_grad()
    output = tensor_to_distribution(model(data.state[:2 * split, 0],
                                          data.action[:2 * split, 0]))

    loss = exact_mll(output, data.next_state[:2 * split, 0].T, model.gp)
    loss.backward()

    optimizer.step()
    print('Iter %d/%d - Loss: %.3f' % (i + 1, 100, loss.item()))

print([(n, p) for (n, p) in model.named_parameters()])
print([(gp.output_scale, gp.length_scale) for gp in model.gp])

model.add_data(data.state[2 * split:, 0], data.action[2 * split:, 0],
               data.next_state[2 * split:, 0])

model.gp[0].output_scale = torch.tensor(1.)
model.gp[0].length_scale = torch.tensor([[9.0]])
model.likelihood[0].noise = torch.tensor([1e-4])

model.gp[1].output_scale = torch.tensor(1.)
model.gp[1].length_scale = torch.tensor([[9.0]])
model.likelihood[1].noise = torch.tensor([1e-4])

model.eval()
dynamic_model = TransformedModel(model, transformations)

# with gpytorch.settings.fast_pred_var(), gpytorch.settings.trace_mode():
#     s, a = data.state.expand(15, 200, 2), data.action.expand(15, 200, 1)
#     dynamic_model(s, a)
#     dynamic_model = torch.jit.trace(dynamic_model, (s, a))

freeze_parameters(dynamic_model)
value_function = NNValueFunction(dim_state=2, layers=[64, 64], biased_head=False,
                                 input_transform=StateTransform())

policy = NNPolicy(dim_state=2, dim_action=1, layers=[64, 64], biased_head=False,
                  squashed_output=True, input_transform=StateTransform())

value_function = torch.jit.script(value_function)
init_distribution = torch.distributions.Uniform(torch.tensor([-np.pi, -0.05]),
                                                torch.tensor([np.pi, 0.05]))

# Initialize MPPO and optimizer.
mppo = MBMPPO(dynamic_model, reward_model, policy, value_function,
              epsilon=0.1, epsilon_mean=0.01, epsilon_var=0.00, gamma=0.99,
              num_action_samples=15)

optimizer = optim.Adam([p for p in mppo.parameters() if p.requires_grad], lr=5e-4)

# %%  Train Controller
num_iter = 100
num_simulation_steps = 400
batch_size = 64
refresh_interval = 2
num_inner_iterations = 30
num_trajectories = math.ceil(num_inner_iterations * 100 / num_simulation_steps)
num_subsample = 1

with gpytorch.settings.fast_pred_var(), gpytorch.settings.detach_test_caches():
    value_losses, policy_losses, policy_returns, eta_parameters, kl_div = train_mppo(
        mppo, init_distribution, optimizer,
        num_iter=num_iter, num_trajectories=num_trajectories,
        num_simulation_steps=num_simulation_steps, refresh_interval=refresh_interval,
        batch_size=batch_size, num_subsample=num_subsample)

plt.plot(refresh_interval * np.arange(len(policy_returns)), policy_returns)
plt.xlabel('Iteration')
plt.ylabel('Cumulative reward')
plt.show()

plot_learning_losses(policy_losses, value_losses, horizon=20)
plt.show()


# %% Test controller on Model.
test_state = torch.tensor(np.array([np.pi, 0.]), dtype=torch.get_default_dtype())

with torch.no_grad():
    trajectory = rollout_model(mppo.dynamical_model, mppo.reward_model,
                               lambda x: (policy(x)[0], torch.zeros(1)),
                               initial_state=test_state.unsqueeze(0).unsqueeze(1),
                               max_steps=400)

    trajectory = Observation(*stack_list_of_tuples(trajectory))

states = trajectory.state[0]
rewards = trajectory.reward
fig, (ax1, ax2) = plt.subplots(ncols=2, figsize=(15, 5))

plt.sca(ax1)
plt.plot(states[0, :, 0], states[0, :, 1], 'x')
plt.plot(states[0, -1, 0], states[0, -1, 1], 'x')
plt.xlabel('Angle [rad]')
plt.ylabel('Angular velocity [rad/s]')

plt.sca(ax2)
plt.plot(rewards[:, 0, 0])
plt.xlabel('Time step')
plt.ylabel('Instantaneous reward')
plt.show()
print(f'Model Cumulative reward: {torch.sum(rewards):.2f}')

bounds = [(-2 * np.pi, 2 * np.pi), (-12, 12)]
ax_value, ax_policy = plot_values_and_policy(value_function, policy, bounds, [200, 200])
ax_value.plot(states[0, :, 0], states[0, :, 1], color='C1')
ax_value.plot(states[0, -1, 0], states[0, -1, 1], 'x', color='C1')
plt.show()

# %% Test controller on Environment.
environment = SystemEnvironment(InvertedPendulum(mass=0.3, length=0.5, friction=0.005,
                                                 step_size=1 / 80), reward=reward_model)
environment.state = test_state.numpy()
environment.initial_state = lambda: test_state.numpy()
trajectory = rollout_policy(environment, lambda x: (policy(x)[0], torch.zeros(1)),
                            max_steps=400, render=True)
trajectory = Observation(*stack_list_of_tuples(trajectory[0]))
print(f'Environment Cumulative reward: {torch.sum(trajectory.reward):.2f}')

