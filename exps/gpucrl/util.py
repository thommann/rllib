"""Utilities for GP-UCRL experiments."""
from itertools import chain

import gpytorch
import numpy as np
import torch
import torch.jit
import torch.nn as nn
import torch.optim as optim

from rllib.agent import MBMPOAgent, MBSACAgent, MPCAgent
from rllib.algorithms.mpc import CEMShooting, MPPIShooting, RandomShooting
from rllib.model.derived_model import OptimisticModel, TransformedModel
from rllib.model.ensemble_model import EnsembleModel
from rllib.model.gp_model import ExactGPModel, RandomFeatureGPModel, SparseGPModel
from rllib.model.nn_model import NNModel
from rllib.policy import MPCPolicy, NNPolicy
from rllib.util.neural_networks import init_head_weight, zero_bias
from rllib.util.training import evaluate_agent, train_agent
from rllib.value_function import NNQFunction, NNValueFunction


def _get_model(
    dim_state, dim_action, params, input_transform=None, transformations=None
):
    transformations = list() if not transformations else transformations

    state = torch.zeros(1, dim_state[0])
    action = torch.zeros(1, dim_action[0])
    next_state = torch.zeros(1, dim_state[0])
    if params.model_kind == "ExactGP":
        model = ExactGPModel(
            state,
            action,
            next_state,
            max_num_points=params.model_max_num_points,
            input_transform=input_transform,
        )
    elif params.model_kind == "SparseGP":
        model = SparseGPModel(
            state,
            action,
            next_state,
            approximation=params.model_sparse_approximation,
            q_bar=params.model_sparse_q_bar,
            max_num_points=params.model_max_num_points,
            input_transform=input_transform,
        )
    elif params.model_kind == "FeatureGP":
        model = RandomFeatureGPModel(
            state,
            action,
            next_state,
            num_features=params.model_num_features,
            approximation=params.model_feature_approximation,
            max_num_points=params.model_max_num_points,
            input_transform=input_transform,
        )
    elif params.model_kind in ["ProbabilisticEnsemble", "DeterministicEnsemble"]:
        model = EnsembleModel(
            dim_state,
            dim_action,
            num_heads=params.model_num_heads,
            layers=params.model_layers,
            biased_head=not params.model_unbiased_head,
            non_linearity=params.model_non_linearity,
            input_transform=input_transform,
            deterministic=params.model_kind == "DeterministicEnsemble",
        )
    elif params.model_kind in ["ProbabilisticNN", "DeterministicNN"]:
        model = NNModel(
            dim_state,
            dim_action,
            biased_head=not params.model_unbiased_head,
            non_linearity=params.model_non_linearity,
            input_transform=input_transform,
            deterministic=params.model_kind == "DeterministicNN",
        )
    else:
        raise NotImplementedError
    try:  # Select GP initial Model.
        for i in range(model.dim_state[0]):
            model.gp[i].output_scale = torch.tensor(0.1)
            model.gp[i].length_scale = torch.tensor([[9.0]])
            model.likelihood[i].noise = torch.tensor([1e-4])
    except AttributeError:
        pass

    params.update({"model": model.__class__.__name__})

    if params.exploration == "optimistic":
        dynamical_model = OptimisticModel(model, transformations, beta=params.beta)
    else:
        dynamical_model = TransformedModel(model, transformations)

    return dynamical_model


def _get_mpc_policy(
    dynamical_model,
    reward_model,
    params,
    action_scale,
    terminal_reward=None,
    termination=None,
):
    if params.mpc_solver == "cem":
        solver = CEMShooting(
            dynamical_model,
            reward_model,
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
            num_cpu=1,
        )
    elif params.mpc_solver == "random":
        solver = RandomShooting(
            dynamical_model,
            reward_model,
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
            num_cpu=1,
        )

    elif params.mpc_solver == "mppi":
        solver = MPPIShooting(
            dynamical_model,
            reward_model,
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
            num_cpu=1,
        )

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
        input_transform=input_transform,
        tau=params.value_function_tau,
    )

    params.update({"value_function": value_function.__class__.__name__})
    # value_function = torch.jit.script(value_function)
    return value_function


def _get_q_function(dim_state, dim_action, params, input_transform=None):
    if params.exploration == "optimistic":
        dim_action = (dim_action[0] + dim_state[0],)

    q_function = NNQFunction(
        dim_state=dim_state,
        dim_action=dim_action,
        layers=params.q_function_layers,
        biased_head=not params.q_function_unbiased_head,
        non_linearity=params.q_function_non_linearity,
        input_transform=input_transform,
        tau=params.q_function_tau,
    )

    params.update({"q_function": q_function.__class__.__name__})
    # value_function = torch.jit.script(value_function)
    return q_function


def _get_nn_policy(dim_state, dim_action, params, action_scale, input_transform=None):
    if params.exploration == "optimistic":
        dim_action = (dim_action[0] + dim_state[0],)

    policy = NNPolicy(
        dim_state=dim_state,
        dim_action=dim_action,
        layers=params.policy_layers,
        biased_head=not params.policy_unbiased_head,
        non_linearity=params.policy_non_linearity,
        squashed_output=True,
        input_transform=input_transform,
        action_scale=action_scale,
        deterministic=params.policy_deterministic,
        tau=params.policy_tau,
    )
    params.update({"policy": policy.__class__.__name__})
    # policy = torch.jit.script(policy)
    return policy


def get_mb_mpo_agent(
    dim_state,
    dim_action,
    params,
    reward_model,
    transformations,
    action_scale,
    input_transform=None,
    termination=None,
    initial_distribution=None,
):
    """Get a MB-MPO agent."""
    # Define Base Model
    dynamical_model = _get_model(
        dim_state, dim_action, params, input_transform, transformations
    )

    # Define Optimistic or Expected Model
    model_optimizer = optim.Adam(
        dynamical_model.parameters(),
        lr=params.model_opt_lr,
        weight_decay=params.model_opt_weight_decay,
    )

    # Define Value function.
    value_function = _get_value_function(dim_state, params, input_transform)

    # Define Policy
    policy = _get_nn_policy(
        dim_state,
        dim_action,
        params,
        action_scale=action_scale,
        input_transform=input_transform,
    )

    assert (
        policy.nn.hidden_layers[0].in_features
        == value_function.nn.hidden_layers[0].in_features
    )

    # zero_bias(policy)
    # init_head_weight(policy)
    # zero_bias(value_function)
    # init_head_weight(value_function)
    zero_bias(dynamical_model)
    init_head_weight(dynamical_model)

    # Define Agent
    optimizer = optim.Adam(
        chain(policy.parameters(), value_function.parameters()),
        lr=params.mpo_opt_lr,
        weight_decay=params.mpo_opt_weight_decay,
    )

    model_name = dynamical_model.base_model.name
    comment = f"{model_name} {params.exploration.capitalize()} {params.action_cost}"

    agent = MBMPOAgent(
        policy=policy,
        value_function=value_function,
        reward_model=reward_model,
        dynamical_model=dynamical_model,
        model_optimizer=model_optimizer,
        model_learn_num_iter=params.model_learn_num_iter,
        model_learn_batch_size=params.model_learn_batch_size,
        bootstrap=not params.not_bootstrap,
        optimizer=optimizer,
        termination=termination,
        plan_horizon=params.plan_horizon,
        plan_samples=params.plan_samples,
        plan_elites=params.plan_elites,
        mpo_value_learning_criterion=nn.MSELoss,
        mpo_epsilon=params.mpo_epsilon,
        mpo_epsilon_mean=params.mpo_epsilon_mean,
        mpo_epsilon_var=params.mpo_epsilon_var,
        mpo_regularization=params.mpo_regularization,
        mpo_num_action_samples=params.mpo_num_action_samples,
        mpo_num_iter=params.mpo_num_iter,
        mpo_gradient_steps=params.mpo_gradient_steps,
        mpo_batch_size=params.mpo_batch_size,
        mpo_target_update_frequency=params.mpo_target_update_frequency,
        sim_num_steps=params.sim_num_steps,
        sim_initial_states_num_trajectories=params.sim_initial_states_num_trajectories,
        sim_initial_dist_num_trajectories=params.sim_initial_dist_num_trajectories,
        sim_memory_num_trajectories=params.sim_memory_num_trajectories,
        sim_num_subsample=params.sim_num_subsample,
        sim_max_memory=params.sim_max_memory,
        sim_refresh_interval=params.sim_refresh_interval,
        thompson_sampling=params.exploration == "thompson",
        initial_distribution=initial_distribution,
        max_memory=params.max_memory,
        gamma=params.gamma,
        comment=comment,
    )

    return agent


def get_mb_sac_agent(
    dim_state,
    dim_action,
    params,
    reward_model,
    transformations,
    action_scale,
    input_transform=None,
    termination=None,
    initial_distribution=None,
):
    """Get SAC Agent."""
    # Define Base Model
    dynamical_model = _get_model(
        dim_state, dim_action, params, input_transform, transformations
    )

    # Define Optimistic or Expected Model
    model_optimizer = optim.Adam(
        dynamical_model.parameters(),
        lr=params.model_opt_lr,
        weight_decay=params.model_opt_weight_decay,
    )

    # Define Value function.
    q_function = _get_q_function(dim_state, dim_action, params, input_transform)

    # Define Policy
    policy = _get_nn_policy(
        dim_state,
        dim_action,
        params,
        action_scale=action_scale,
        input_transform=input_transform,
    )

    # Define Agent
    optimizer = optim.Adam(
        chain(q_function.parameters(), policy.parameters()),
        lr=params.sac_opt_lr,
        weight_decay=params.sac_opt_weight_decay,
    )

    assert (
        policy.nn.hidden_layers[0].in_features + policy.nn.head.out_features
        == q_function.nn.hidden_layers[0].in_features
    )

    model_name = dynamical_model.base_model.name
    comment = f"{model_name} {params.exploration.capitalize()} {params.action_cost}"

    zero_bias(policy)
    init_head_weight(policy)
    # zero_bias(q_function)
    # init_head_weight(q_function)
    zero_bias(dynamical_model)
    init_head_weight(dynamical_model)

    agent = MBSACAgent(
        dynamical_model=dynamical_model,
        reward_model=reward_model,
        policy=policy,
        q_function=q_function,
        sac_value_learning_criterion=nn.MSELoss,
        optimizer=optimizer,
        model_optimizer=model_optimizer,
        model_learn_num_iter=params.model_learn_num_iter,
        model_learn_batch_size=params.model_learn_batch_size,
        bootstrap=not params.not_bootstrap,
        plan_horizon=params.plan_horizon,
        plan_samples=params.plan_samples,
        plan_elites=params.plan_elites,
        sac_eta=params.sac_eta,
        sac_regularization=params.sac_regularization,
        sac_num_iter=params.sac_num_iter,
        sac_gradient_steps=params.sac_gradient_steps,
        sac_batch_size=params.sac_batch_size,
        sac_target_update_frequency=params.sac_target_update_frequency,
        sac_target_num_steps=params.sac_target_num_steps,
        sac_num_samples=params.sac_num_samples,
        sim_num_steps=params.sim_num_steps,
        sim_initial_states_num_trajectories=params.sim_initial_states_num_trajectories,
        sim_initial_dist_num_trajectories=params.sim_initial_dist_num_trajectories,
        sim_memory_num_trajectories=params.sim_memory_num_trajectories,
        sim_num_subsample=params.sim_num_subsample,
        sim_max_memory=params.sim_max_memory,
        sim_refresh_interval=params.sim_refresh_interval,
        thompson_sampling=params.exploration == "thompson",
        initial_distribution=initial_distribution,
        max_memory=params.max_memory,
        gamma=params.gamma,
        comment=comment,
    )

    return agent


def get_mpc_agent(
    dim_state,
    dim_action,
    params,
    reward_model,
    transformations,
    action_scale,
    input_transform=None,
    termination=None,
    initial_distribution=None,
):
    """Get an MPC based agent."""
    # Define Base Model
    dynamical_model = _get_model(
        dim_state, dim_action, params, input_transform, transformations
    )

    # Define Optimistic or Expected Model
    model_optimizer = optim.Adam(
        dynamical_model.parameters(),
        lr=params.model_opt_lr,
        weight_decay=params.model_opt_weight_decay,
    )

    # Define Value function.
    value_function = _get_value_function(dim_state, params, input_transform)

    value_optimizer = optim.Adam(
        value_function.parameters(),
        lr=params.value_opt_lr,
        weight_decay=params.value_opt_weight_decay,
    )

    if params.mpc_terminal_reward:
        terminal_reward = value_function
    else:
        terminal_reward = None

    # Define Policy
    policy = _get_mpc_policy(
        dynamical_model,
        reward_model,
        params,
        action_scale=action_scale,
        terminal_reward=terminal_reward,
        termination=termination,
    )

    # Define Agent
    model_name = dynamical_model.base_model.name
    comment = f"{model_name} {params.exploration.capitalize()} {params.action_cost}"

    agent = MPCAgent(
        policy,
        model_optimizer=model_optimizer,
        model_learn_num_iter=params.model_learn_num_iter,
        model_learn_batch_size=params.model_learn_batch_size,
        bootstrap=not params.not_bootstrap,
        value_optimizer=value_optimizer,
        value_opt_num_iter=params.value_opt_num_iter,
        value_opt_batch_size=params.value_opt_batch_size,
        value_gradient_steps=params.value_gradient_steps,
        value_num_steps_returns=params.value_num_steps_returns,
        sim_num_steps=params.sim_num_steps,
        sim_initial_states_num_trajectories=params.sim_initial_states_num_trajectories,
        sim_initial_dist_num_trajectories=params.sim_initial_dist_num_trajectories,
        sim_memory_num_trajectories=params.sim_memory_num_trajectories,
        thompson_sampling=params.exploration == "thompson",
        initial_distribution=initial_distribution,
        max_memory=params.max_memory,
        gamma=params.gamma,
        comment=comment,
    )

    return agent


def large_state_termination(state, action, next_state=None):
    """Termination condition for environment."""
    if not isinstance(state, torch.Tensor):
        state = torch.tensor(state)
    if not isinstance(action, torch.Tensor):
        action = torch.tensor(action)

    return torch.any(torch.abs(state) > 200, dim=-1) | torch.any(
        torch.abs(action) > 15, dim=-1
    )


def train_and_evaluate(
    agent, environment, params, plot_callbacks=None, save_milestones=None
):
    """Train and evaluate agent on environment."""
    # %% Train Agent
    agent.logger.save_hparams(params.toDict())
    with gpytorch.settings.fast_computations(), gpytorch.settings.fast_pred_var(), (
        gpytorch.settings.fast_pred_samples()
    ), (gpytorch.settings.memory_efficient()):
        train_agent(
            agent,
            environment,
            num_episodes=params.train_episodes,
            max_steps=params.environment_max_steps,
            plot_flag=params.plot_train_results,
            print_frequency=params.print_frequency,
            save_milestones=save_milestones,
            render=params.render_train,
            plot_callbacks=plot_callbacks,
        )
    agent.logger.export_to_json()  # Save statistics.

    # %% Test agent.
    metrics = dict()
    evaluate_agent(
        agent,
        environment,
        num_episodes=params.test_episodes,
        max_steps=params.environment_max_steps,
        render=params.render_test,
    )

    returns = np.mean(agent.logger.get("environment_return")[-params.test_episodes :])
    metrics.update({"test/test_env_returns": returns})
    returns = np.mean(agent.logger.get("environment_return")[: -params.test_episodes])
    metrics.update({"test/train_env_returns": returns})

    agent.logger.log_hparams(params.toDict(), metrics)
