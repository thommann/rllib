"""Run Reacher experiments."""

import os

from lsf_runner import init_runner, make_commands

from exps.gpucrl.reacher import ACTION_COST

runner = init_runner(
    f"GPUCRL_Reacher_mpc", num_threads=1, wall_time=1439, num_workers=4
)

cmd_list = make_commands(
    "mpc.py",
    base_args={"num-threads": 1},
    fixed_hyper_args={},
    common_hyper_args={
        "seed": [2],
        "exploration": ["optimistic"],
        "model-kind": ["ProbabilisticEnsemble"],
        "action-cost": [0, ACTION_COST, 5 * ACTION_COST, 10 * ACTION_COST],
    },
    algorithm_hyper_args={},
)
runner.run(cmd_list)
if "AWS" in os.environ:
    os.system("sudo shutdown")
