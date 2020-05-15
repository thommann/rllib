"""Utilities for GP-UCRL experiments."""
import os
import json

import torch
import torch.jit
import torch.optim as optim
import gpytorch
import numpy as np
from dotmap import DotMap
import pandas as pd

from rllib.agent import MPCAgent, MBMPPOAgent
from rllib.algorithms.mpc import CEMShooting, RandomShooting, MPPIShooting
from rllib.algorithms.mppo import MBMPPO
from rllib.model.gp_model import ExactGPModel, RandomFeatureGPModel, SparseGPModel
from rllib.model.nn_model import NNModel
from rllib.model.ensemble_model import EnsembleModel
from rllib.model.derived_model import OptimisticModel, TransformedModel
from rllib.policy import MPCPolicy, NNPolicy
from rllib.value_function import NNValueFunction
from rllib.util.training import train_agent, evaluate_agent


def _get_model(dim_state, dim_action, params, input_transform=None,
               transformations=None):
    transformations = list() if not transformations else transformations

    state = torch.zeros(1, dim_state)
    action = torch.zeros(1, dim_action)
    next_state = torch.zeros(1, dim_state)
    if params.model_kind == 'ExactGP':
        model = ExactGPModel(state, action, next_state,
                             max_num_points=params.model_max_num_points,
                             input_transform=input_transform)
    elif params.model_kind == 'SparseGP':
        model = SparseGPModel(state, action, next_state,
                              approximation=params.model_sparse_approximation,
                              q_bar=params.model_sparse_q_bar,
                              max_num_points=params.model_max_num_points,
                              input_transform=input_transform
                              )
    elif params.model_kind == 'FeatureGP':
        model = RandomFeatureGPModel(
            state, action, next_state,
            num_features=params.model_num_features,
            approximation=params.model_feature_approximation,
            max_num_points=params.model_max_num_points,
            input_transform=input_transform)
    elif params.model_kind in ['ProbabilisticEnsemble', 'DeterministicEnsemble']:
        model = EnsembleModel(
            dim_state, dim_action,
            num_heads=params.model_num_heads,
            layers=params.model_layers,
            biased_head=not params.model_unbiased_head,
            non_linearity=params.model_non_linearity,
            input_transform=input_transform,
            deterministic=params.model_kind == 'DeterministicEnsemble')
    elif params.model_kind in ['ProbabilisticNN', 'DeterministicNN']:
        model = NNModel(
            dim_state, dim_action,
            biased_head=not params.model_unbiased_head,
            non_linearity=params.model_non_linearity,
            input_transform=input_transform,
            deterministic=params.model_kind == 'DeterministicNN')
    else:
        raise NotImplementedError
    try:  # Select GP initial Model.
        for i in range(model.dim_state):
            model.gp[i].output_scale = torch.tensor(0.1)
            model.gp[i].length_scale = torch.tensor([[9.0]])
            model.likelihood[i].noise = torch.tensor([1e-4])
    except AttributeError:
        pass

    params.update({"model": model.__class__.__name__})

    if params.exploration == 'optimistic':
        dynamical_model = OptimisticModel(model, transformations, beta=params.beta)
    else:
        dynamical_model = TransformedModel(model, transformations)

    return dynamical_model


def _get_mpc_policy(dynamical_model, reward_model, params, action_scale,
                    terminal_reward=None, termination=None):
    if params.mpc_solver == 'cem':
        solver = CEMShooting(dynamical_model, reward_model,
                             horizon=params.mpc_horizon,
                             gamma=params.gamma,
                             scale=1 / 8,
                             action_scale=action_scale,
                             num_iter=params.mpc_num_iter,
                             num_samples=params.mpc_num_samples,
                             num_elites=params.mpc_num_elites,
                             alpha=params.mpc_alpha,
                             terminal_reward=terminal_reward,
                             termination=termination,
                             warm_start=not params.mpc_not_warm_start,
                             default_action=params.mpc_default_action,
                             num_cpu=1)
    elif params.mpc_solver == 'random':
        solver = RandomShooting(dynamical_model, reward_model,
                                horizon=params.mpc_horizon,
                                gamma=params.gamma,
                                action_scale=action_scale,
                                scale=1 / 3,
                                num_samples=params.mpc_num_samples,
                                num_elites=params.mpc_num_elites,
                                terminal_reward=terminal_reward,
                                termination=termination,
                                warm_start=not params.mpc_not_warm_start,
                                default_action=params.mpc_default_action,
                                num_cpu=1)

    elif params.mpc_solver == 'mppi':
        solver = MPPIShooting(dynamical_model, reward_model,
                              horizon=params.mpc_horizon,
                              gamma=params.gamma,
                              action_scale=action_scale,
                              scale=1 / 8,
                              num_iter=params.mpc_num_iter,
                              num_samples=params.mpc_num_samples,
                              terminal_reward=terminal_reward,
                              termination=termination,
                              warm_start=not params.mpc_not_warm_start,
                              default_action=params.mpc_default_action,
                              kappa=params.mpc_kappa,
                              filter_coefficients=params.mpc_filter_coefficients,
                              num_cpu=1)

    else:
        raise NotImplementedError(f"{params.mpc_solver.capitalize()} not recognized.")
    policy = MPCPolicy(solver)
    return policy


def _get_value_function(dim_state, params, input_transform=None):
    value_function = NNValueFunction(
        dim_state=dim_state,
        layers=params.value_function_layers,
        biased_head=not params.value_function_unbiased_head,
        non_linearity=params.value_function_non_linearity,
        input_transform=input_transform)

    params.update({"value_function": value_function.__class__.__name__})
    # value_function = torch.jit.script(value_function)
    return value_function


def _get_nn_policy(dim_state, dim_action, params, action_scale, input_transform=None):
    if params.exploration == 'optimistic':
        dim_action += dim_state

    policy = NNPolicy(
        dim_state=dim_state, dim_action=dim_action,
        layers=params.policy_layers,
        biased_head=not params.policy_unbiased_head,
        non_linearity=params.policy_non_linearity,
        squashed_output=True,
        input_transform=input_transform,
        action_scale=action_scale,
        deterministic=params.policy_deterministic)
    params.update({"policy": policy.__class__.__name__})
    # policy = torch.jit.script(policy)
    return policy


def get_mb_mppo_agent(env_name, dim_state, dim_action, params, reward_model,
                      transformations, action_scale, input_transform=None,
                      termination=None, initial_distribution=None):
    """Get a MB-MPPO agent."""
    # Define Base Model
    dynamical_model = _get_model(dim_state, dim_action, params, input_transform,
                                 transformations)

    # Define Optimistic or Expected Model
    model_optimizer = optim.Adam(dynamical_model.parameters(),
                                 lr=params.model_opt_lr,
                                 weight_decay=params.model_opt_weight_decay)

    # Define Value function.
    value_function = _get_value_function(dynamical_model.dim_state, params,
                                         input_transform)

    # Define Policy
    policy = _get_nn_policy(dim_state, dim_action, params, action_scale=action_scale,
                            input_transform=input_transform)

    # Define Agent
    mppo = MBMPPO(dynamical_model, reward_model, policy, value_function,
                  eta=params.mppo_eta, eta_mean=params.mppo_eta_mean,
                  eta_var=params.mppo_eta_var, gamma=params.gamma,
                  num_action_samples=params.mppo_num_action_samples,
                  termination=termination)

    mppo_optimizer = optim.Adam([p for name, p in mppo.named_parameters()
                                 if 'model' not in name],
                                lr=params.mppo_opt_lr,
                                weight_decay=params.mppo_opt_weight_decay)

    model_name = dynamical_model.base_model.name
    comment = f"{model_name} {params.exploration.capitalize()} {params.action_cost}"

    agent = MBMPPOAgent(
        env_name,
        mppo=mppo,
        model_optimizer=model_optimizer,
        model_learn_num_iter=params.model_learn_num_iter,
        model_learn_batch_size=params.model_learn_batch_size,
        mppo_optimizer=mppo_optimizer,
        plan_horizon=params.plan_horizon,
        plan_samples=params.plan_samples,
        plan_elite=params.plan_elite,
        mppo_num_iter=params.mppo_num_iter,
        mppo_gradient_steps=params.mppo_gradient_steps,
        mppo_batch_size=params.mppo_batch_size,
        sim_num_steps=params.sim_num_steps,
        sim_initial_states_num_trajectories=params.sim_initial_states_num_trajectories,
        sim_initial_dist_num_trajectories=params.sim_initial_dist_num_trajectories,
        sim_memory_num_trajectories=params.sim_memory_num_trajectories,
        sim_num_subsample=params.sim_num_subsample,
        thompson_sampling=params.exploration == 'thompson',
        initial_distribution=initial_distribution,
        max_memory=params.max_memory,
        gamma=params.gamma,
        comment=comment
    )

    return agent


def get_mpc_agent(env_name, dim_state, dim_action, params, reward_model,
                  transformations, action_scale, input_transform=None, termination=None,
                  initial_distribution=None):
    """Get an MPC based agent."""
    # Define Base Model
    dynamical_model = _get_model(dim_state, dim_action, params, input_transform,
                                 transformations)

    # Define Optimistic or Expected Model
    model_optimizer = optim.Adam(dynamical_model.parameters(),
                                 lr=params.model_opt_lr,
                                 weight_decay=params.model_opt_weight_decay)

    # Define Value function.
    value_function = _get_value_function(dynamical_model.dim_state, params,
                                         input_transform)

    value_optimizer = optim.Adam(value_function.parameters(),
                                 lr=params.value_opt_lr,
                                 weight_decay=params.value_opt_weight_decay)

    if params.mpc_terminal_reward:
        terminal_reward = value_function
    else:
        terminal_reward = None

    # Define Policy
    policy = _get_mpc_policy(dynamical_model, reward_model, params,
                             action_scale=action_scale, terminal_reward=terminal_reward,
                             termination=termination)

    # Define Agent
    model_name = dynamical_model.base_model.name
    comment = f"{model_name} {params.exploration.capitalize()} {params.action_cost}"

    agent = MPCAgent(
        env_name, policy,
        model_optimizer=model_optimizer,
        model_learn_num_iter=params.model_learn_num_iter,
        model_learn_batch_size=params.model_learn_batch_size,
        value_optimizer=value_optimizer,
        value_opt_num_iter=params.value_opt_num_iter,
        value_opt_batch_size=params.value_opt_batch_size,
        value_gradient_steps=params.value_gradient_steps,
        value_num_steps_returns=params.value_num_steps_returns,
        sim_num_steps=params.sim_num_steps,
        sim_initial_states_num_trajectories=params.sim_initial_states_num_trajectories,
        sim_initial_dist_num_trajectories=params.sim_initial_dist_num_trajectories,
        sim_memory_num_trajectories=params.sim_memory_num_trajectories,
        thompson_sampling=params.exploration == 'thompson',
        initial_distribution=initial_distribution,
        max_memory=params.max_memory,
        gamma=params.gamma,
        comment=comment
    )

    return agent


def large_state_termination(state, action, next_state=None):
    """Termination condition for environment."""
    if not isinstance(state, torch.Tensor):
        state = torch.tensor(state)
    if not isinstance(action, torch.Tensor):
        action = torch.tensor(action)

    return (torch.any(torch.abs(state) > 200, dim=-1) | torch.any(
        torch.abs(action) > 15, dim=-1))


def train_and_evaluate(agent, environment, params, plot_callbacks):
    """Train and evaluate agent on environment."""
    # %% Train Agent
    with gpytorch.settings.fast_computations(), gpytorch.settings.fast_pred_var(), \
         gpytorch.settings.fast_pred_samples(), gpytorch.settings.memory_efficient():
        train_agent(agent, environment,
                    num_episodes=params.train_episodes,
                    max_steps=params.environment_max_steps * environment.frame_skip,
                    plot_flag=params.plot_train_results,
                    print_frequency=params.print_frequency,
                    render=params.render_train,
                    plot_callbacks=plot_callbacks
                    )
    agent.logger.export_to_json(params.toDict())

    # %% Test agent.
    metrics = dict()
    evaluate_agent(agent, environment, num_episodes=params.test_episodes,
                   max_steps=params.environment_max_steps * environment.frame_skip,
                   render=params.render_test)

    returns = np.mean(agent.logger.get('environment_return')[-params.test_episodes:])
    metrics.update({"test/test_env_returns": returns})
    returns = np.mean(agent.logger.get('environment_return')[:-params.test_episodes])
    metrics.update({"test/train_env_returns": returns})

    agent.logger.log_hparams(params.toDict(), metrics)


def parse_results(base_dir):
    """Parse all results from base directory."""
    log_dirs = os.listdir(base_dir)

    results = {}
    for log_dir in log_dirs:
        try:
            with open(f"{base_dir}{log_dir}/hparams.json", 'r') as f:
                params = DotMap(json.load(f))
        except FileNotFoundError:  # If experiment did not finish, just continue.
            continue
        exploration = params.get('exploration',
                                 'optimistic' if params.optimistic else 'expected')
        name = f"{params.action_cost}{exploration}{params.model_kind}"

        with open(f"{base_dir}{log_dir}/statistics.json", 'r') as f:
            df = pd.read_json(f)

        if name not in results:
            results[name] = (df.iloc[-1].environment_return, df, params)
        else:
            if df.iloc[-1].environment_return > results[name][0]:
                results[name] = (df.iloc[-1].environment_return, df, params)

    return results