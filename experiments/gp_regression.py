import math

import gpytorch
import matplotlib.pyplot as plt
import numpy as np
import torch

from rllib.util.gaussian_processes.gps import ExactGP, SparseGP, RandomFeatureGP

torch.manual_seed(0)
np.random.seed(0)


def f(x):
    return torch.sin(2 * x) / x


num_training = 50
train_x = torch.randn(num_training) * 4
train_y = f(train_x) + torch.randn(len(train_x)) * 1e-4
train_y = train_y
idx = np.random.choice(num_training, 25, replace=False)
inducing_points = train_x[idx].unsqueeze(-1)
# train_x, train_y = inducing_points, train_y[idx]
# inducing_points = 5 * torch.randn(200, 1)
plt.show()

likelihood = gpytorch.likelihoods.GaussianLikelihood()
for name, model in {
    'Exact': ExactGP(train_x, train_y, likelihood=likelihood),
    'RFF': RandomFeatureGP(train_x.unsqueeze(-1), train_y, likelihood=likelihood,
                           num_features=100, approximation='rff'),
    'QFF': RandomFeatureGP(train_x.unsqueeze(-1), train_y, likelihood=likelihood,
                           num_features=10, approximation='qff'),
    # 'DTC': SparseGP(train_x, train_y, likelihood=likelihood,
    #                 inducing_points=inducing_points, approximation='DTC'),
    # 'SOR': SparseGP(train_x, train_y, likelihood=likelihood,
    #                 inducing_points=inducing_points, approximation='SOR'),
    # 'FITC': SparseGP(train_x, train_y, likelihood=likelihood,
    #                  inducing_points=inducing_points, approximation='FITC'),
}.items():
    np.random.seed(0)
    model.likelihood.noise = torch.tensor([1e-2])
    model.output_scale = torch.tensor(0.1)
    model.length_scale = torch.tensor([0.2])
    model.eval()
    test_x = torch.arange(-10., 10., 0.01)
    with torch.no_grad(), gpytorch.settings.fast_pred_var(), \
         gpytorch.settings.fast_pred_var(), gpytorch.settings.fast_pred_samples():
        k = model(torch.arange(-10., 10., 1.).unsqueeze(-1).unsqueeze(0))
        print(name)
        print(k.mean)
        print(k.variance)

        out = model(test_x.unsqueeze(-1))

        lower, upper = out.confidence_region()

        plt.plot(train_x.numpy(), train_y.numpy(), 'k*')
        # Plot predictive means as blue line
        plt.plot(test_x.numpy(), out.mean.numpy(), 'b')
        plt.plot(test_x.numpy(), f(test_x).numpy(), 'k-')
        # Shade between the lower and upper confidence bounds
        plt.fill_between(test_x.numpy(), lower.numpy(), upper.numpy(), alpha=0.5)

        if name == 'Sparse':
            plt.plot(model.xu.numpy(),
                     (model.prediction_strategy.train_labels * model.likelihood.noise
                      ).numpy(), 'r*')
    plt.title(name)
    plt.show()
