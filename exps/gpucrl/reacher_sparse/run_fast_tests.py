"""Run Reacher experiments."""

from lsf_runner import make_commands, init_runner
from exps.gpucrl.reacher_sparse import ACTION_COST
import os


runner = init_runner(f'GPUCRL_Reacher_sparse_mpc', num_threads=1, wall_time=1439,
                     num_workers=12)

cmd_list = make_commands(
    'mpc.py',
    base_args={'num-threads': 1},
    fixed_hyper_args={},
    common_hyper_args={
        'seed': [0],
        'exploration': ['thompson', 'optimistic', 'expected'],
        'model-kind': ['ProbabilisticEnsemble'],
        'action-cost': [0, ACTION_COST, 5 * ACTION_COST, 10 * ACTION_COST],
    },
    algorithm_hyper_args={},
)
runner.run(cmd_list)
if 'AWS' in os.environ:
    os.system("sudo shutdown")