"""Exact GP Model."""
import gpytorch
from .prediction_strategies import SparsePredictionStrategy
from .utilities import cartesian
from gpytorch.models.exact_prediction_strategies import DefaultPredictionStrategy
from gpytorch.lazy import MatmulLazyTensor, lazify, delazify
import torch
import numpy as np
from scipy.stats.distributions import chi


class ExactGP(gpytorch.models.ExactGP):
    r"""Exact GP Model.

    A GP Model outputs at location `x' a Multivariate Normal given by:

    ..math:: A = [K(x_t, x_t) + \sigma^2 I]^-1
    ..math:: \mu(x) = m(x) + K(x, x_t)^\top A (y_t - m(x_t))
    ..math:: \Sigma(x) = K(x, x) - K(x, x_t)^\top A K(x_t, x)

    Parameters
    ----------
    train_x: Tensor
        Tensor of dimension N x dim_x.
    train_y: Tensor
        Tensor with dimension N.
    likelihood: Likelihood
        Model Likelihood.
    mean: Mean
        Mean module, optional. By default zero mean.
    kernel: Kernel.
        Kernel module, optional. By default RBF kernel.

    References
    ----------
    Williams, C. K., & Rasmussen, C. E. (2006).
    Gaussian processes for machine learning. MIT press.
    """

    def __init__(self, train_x, train_y, likelihood, mean=None, kernel=None):
        super().__init__(train_x, train_y, likelihood)
        if mean is None:
            mean = gpytorch.means.ZeroMean()
        self.mean_module = mean

        if kernel is None:
            kernel = gpytorch.kernels.ScaleKernel(gpytorch.kernels.RBFKernel())
        self.covar_module = kernel

    @property
    def output_scale(self):
        """Get output scale."""
        return self.covar_module.outputscale

    @output_scale.setter
    def output_scale(self, new_output_scale):
        """Set output scale."""
        self.covar_module.outputscale = new_output_scale

    @property
    def length_scale(self):
        """Get length scale."""
        return self.covar_module.base_kernel.lengthscale

    @length_scale.setter
    def length_scale(self, new_length_scale):
        """Set length scale."""
        self.covar_module.base_kernel.lengthscale = new_length_scale

    def forward(self, x):
        """Forward computation of GP."""
        mean_x = self.mean_module(x)
        covar_x = self.covar_module(x)
        return gpytorch.distributions.MultivariateNormal(mean_x, covar_x)


class SparseGP(ExactGP):
    r"""Sparse GP Models.

    A Sparse GP approximates the GP via a set of inducing points u.

    Subset of Regressions (SOR) approximation:
    Similar to RFFs, it has variance starvation.
    ..math:: A = [K(u, u) + K(u, x_t) K(x_t, u) / \sigma^2 ]^{-1}.
    ..math:: \mu(x) = K(x, u) A K(u, x_t) y / \sigma^2
    ..math:: \Sigma(x) = K(x, u) A K(u, x)

    Deterministic Training Conditional (DTC) approximation:
    Better predictive variance than SOR.
    ..math:: A = [ K(u, u) + K(u, x_t) K(x_t, u) / \sigma^2]^{-1}.
    ..math:: Q(x, x) = K(x, u) K(u,u)^{-1} K(u, x)
    ..math:: \mu(x) = K(x, u) A K(u, x_t) y / \sigma^2
    ..math:: \Sigma(x) = K(x, x) + K(x, u) A K(u, x) - Q(x, x)

    Fully Independent Training Condiitonal (FITC) approximation:
    ..math:: D = diag(K(x_t, x_t) - Q(x_t, x_t) + \sigma^2)
    ..math:: A = [K(u, u) + K(u, x_t) D^{-1}K(x_t, u)]^{-1}
    ..math:: \mu = K(x, u) A K(u, x_t) D^{-1} y
    ..math:: \Sigma = K(x, x) + K(x, u) A K(u, x) - Q(x, x)

    Parameters
    ----------
    train_x: Tensor
        Tensor of dimension N x dim_x.
    train_y: Tensor
        Tensor with dimension N.
    inducing_points: Tensor
        Tensor of dimension N x dim_x.
    likelihood: Likelihood
        Model Likelihood.
    mean: Mean
        Mean module, optional. By default zero mean.
    kernel: Kernel.
        Kernel module, optional. By default RBF kernel.

    References
    ----------
    Quiñonero-Candela, J., & Rasmussen, C. E. (2005).
    A unifying view of sparse approximate Gaussian process regression. JMLR.
    """

    def __init__(self, train_x, train_y, likelihood, inducing_points,
                 mean=None, kernel=None, approximation='DTC'):
        super().__init__(train_x, train_y, likelihood, mean=mean, kernel=kernel)

        self.prediction_strategy = None
        self.xu = inducing_points
        self.approximation = approximation

    def set_inducing_points(self, inducing_points):
        """Set Inducing Points. Reset caches."""
        self.xu = inducing_points
        self.prediction_strategy = None

    def __call__(self, x):
        """Return GP posterior at location `x'."""
        train_inputs = self.xu
        m = train_inputs.shape[0]
        inputs = x

        if self.prediction_strategy is None:
            x_uf = torch.cat((train_inputs, self.train_inputs[0]), dim=0)
            output = self.forward(x_uf)
            mu_uf, kernel = output.mean, output.lazy_covariance_matrix

            mu_u, mu_f = mu_uf[:m], mu_uf[m:]
            k_uu, k_ff, k_uf = kernel[:m, :m], kernel[m:, m:], kernel[:m, m:]

            if self.approximation == 'FITC':
                k_uu_root_inv = k_uu.root_inv_decomposition().root
                z = k_uf.transpose(-2, -1) @ k_uu_root_inv
                q_ff = z @ z.transpose(-2, -1)

                diag = delazify(k_ff - q_ff).diag() + self.likelihood.noise
                diag = lazify(torch.diag(1 / diag))

            elif self.approximation == 'SOR' or self.approximation == 'DTC':
                diag = lazify(torch.eye(len(self.train_targets))
                              ).mul(1. / self.likelihood.noise)

            cov = k_uu + (k_uf @ diag) @ k_uf.transpose(-2, -1)

            prior_dist = gpytorch.distributions.MultivariateNormal(
                mu_u, cov.add_jitter(1e-3))

            # Create the prediction strategy for
            self.prediction_strategy = SparsePredictionStrategy(
                train_inputs=train_inputs,
                train_prior_dist=prior_dist,
                train_labels=(k_uf @ diag) @ (self.train_targets - mu_f),
                likelihood=self.likelihood,
                k_uu=k_uu.add_jitter()
            )

        # Concatenate the input to the training input
        batch_shape = inputs.shape[:-2]
        # Make sure the batch shapes agree for training/test data
        if batch_shape != train_inputs.shape[:-2]:
            train_inputs = train_inputs.expand(*batch_shape, *train_inputs.shape[-2:])
        full_inputs = torch.cat([train_inputs, inputs], dim=-2)

        # Get the joint distribution for training/test data
        joint_output = self.forward(full_inputs)
        joint_mean, joint_covar = joint_output.loc, joint_output.lazy_covariance_matrix

        # Separate components.
        mu_s = joint_mean[..., m:]
        k_su, k_ss = joint_covar[..., m:, :m], joint_covar[..., m:, m:]

        pred_mean = mu_s + k_su @ self.prediction_strategy.mean_cache

        sig_inv_root = self.prediction_strategy.covar_cache
        k_su_sig_inv_root = k_su @ sig_inv_root
        rhs = MatmulLazyTensor(k_su_sig_inv_root, k_su_sig_inv_root.transpose(-2, -1))

        kuu_inv_root = self.prediction_strategy.k_uu_inv_root
        k_su_kuu_inv_root = k_su @ kuu_inv_root
        q_ss = MatmulLazyTensor(k_su_kuu_inv_root, k_su_kuu_inv_root.transpose(-2, -1))

        if self.approximation == 'DTC' or self.approximation == 'FITC':
            pred_cov = k_ss - q_ss + rhs
        elif self.approximation == 'SOR':
            pred_cov = rhs
        else:
            raise NotImplementedError(f"{self.approximation} Not implemented.")

        return joint_output.__class__(pred_mean, pred_cov)


class RandomFeatureGP(ExactGP):
    r"""Random Feature GP Models.

    Random Feature models approximate a stationary kernel using Bochner's theorem and
    numerical integration. RF Models approximate a stationary kernel k(x, y) = k(x - y)
    with
    ..math:: k(x, y) \approx \phi(x)^\top \phi(y),
    where \phi(x) \in \mathbb{R}^m.

    The approximate posterior distribution is given by:
    ..math:: A = [\Phi(x_t)^\top \Phi(x_t) + \sigma^2 I]^{-1}
    ..math:: \mu(x) = \Phi(x) A \Phi(x_t)^\top y
    ..math:: \Sigma(x) = \sigma^2 \Phi(x) A \Phi(x)^\top

    The crucial aspect is how the features \Phi are generated.

    Random fourier features (RFF) are sampled from the probability distribution induced
    by the kernel fourier transform.

    For QFF these are generated from numerical quadrature.

    For OFF these

    Parameters
    ----------
    train_x: Tensor
        Tensor of dimension N x dim_x.
    train_y: Tensor
        Tensor with dimension N.
    num_features: int
        Number of features to approximate the kernel with.
    approximation: 'str'
        Approximation to Random Features.
    likelihood: Likelihood
        Model Likelihood.
    mean: Mean
        Mean module, optional. By default zero mean.
    kernel: Kernel.
        Kernel module, optional. By default RBF kernel.

    References
    ----------
    Rahimi, A., & Recht, B. (2008).
    Random features for large-scale kernel machines. NeuRIPS.

    Yu, F. et al. (2016).
    Orthogonal random features. NeuRIPS.
    """

    def __init__(self, train_x, train_y, likelihood, num_features, approximation='rff',
                 mean=None, kernel=None):
        super().__init__(train_x, train_y, likelihood, mean=mean, kernel=kernel)
        self._num_features = num_features
        self.approximation = approximation

        self.dim = train_x.shape[-1]

        if kernel is None:
            length_scale, scale = 1., 1.
        elif isinstance(kernel, gpytorch.kernels.RBFKernel):
            length_scale, scale = kernel.lengthscale, kernel.outputscale
        elif isinstance(kernel.base_kernel, gpytorch.kernels.RBFKernel):
            length_scale, scale = kernel.base_kernel.lengthscale, kernel.outputscale
        else:
            raise NotImplementedError

        self._output_scale = scale
        self._length_scale = length_scale

        self.w, self.b, self._feature_scale = self.sample_features()

    def sample_features(self):
        """Sample a set of random features."""
        # Only squared-exponential kernels are implemented.
        if self.approximation == 'rff':
            w = torch.randn(self.num_features, self.dim) / self.length_scale
            scale = torch.tensor(1. / self.num_features)

        elif self.approximation == 'off':
            q, _ = torch.qr(torch.randn(self.num_features, self.dim))
            diag = torch.diag(torch.tensor(
                chi.rvs(df=self.num_features, size=self.num_features),
                dtype=torch.get_default_dtype()))
            w = (diag @ q) / self.length_scale
            scale = torch.tensor(1. / self.num_features)

        elif self.approximation == 'qff':
            q = int(np.power(self.num_features, 1. / self.dim))
            omegas, weights = np.polynomial.hermite.hermgauss(2 * q)

            omegas = np.sqrt(2) * omegas[q:] / np.array(self.length_scale)
            w = torch.tensor(cartesian([omegas for _ in range(self.dim)]),
                             dtype=torch.get_default_dtype())

            weights = 2 * weights[q:] / np.sqrt(np.pi)
            weights = np.prod(cartesian([weights for _ in range(self.dim)]), axis=1)
            scale = torch.sqrt(torch.tensor(weights, dtype=torch.get_default_dtype()))
        else:
            raise NotImplementedError(f"{self.approximation} not implemented.")

        b = 2 * np.pi * torch.rand(self.num_features)
        return w, b, scale

    @property
    def output_scale(self):
        """Get output scale."""
        return self._output_scale

    @output_scale.setter
    def output_scale(self, value):
        """Set output scale."""
        self._output_scale = value

    @property
    def length_scale(self):
        """Get length scale."""
        return self._length_scale

    @length_scale.setter
    def length_scale(self, value):
        """Set length scale."""
        self._length_scale = value
        self.w, self.b, self._feature_scale = self.sample_features()

    @property
    def num_features(self):
        """Get number of features."""
        return self._num_features

    @num_features.setter
    def num_features(self, value):
        """Set number of features."""
        self._num_features = value
        self.w, self.b, self._feature_scale = self.sample_features()

    @property
    def scale(self):
        """Return feature scale."""
        return torch.sqrt(self._feature_scale * self.output_scale)

    def __call__(self, x):
        """Return GP posterior at location `x'."""
        train_inputs = torch.zeros(2 * self.num_features, 1)
        inputs = x

        if self.prediction_strategy is None:
            x = self.train_inputs[0]
            zt = self.forward(x).transpose(-2, -1)

            mean = train_inputs.squeeze(-1)

            cov = lazify(zt @ zt.transpose(-1, -2)).add_jitter()

            y = self.train_targets - self.mean_module(x)
            labels = zt @ y

            prior_dist = gpytorch.distributions.MultivariateNormal(mean, cov)
            self.prediction_strategy = DefaultPredictionStrategy(
                train_inputs=train_inputs,
                train_prior_dist=prior_dist,
                train_labels=labels,
                likelihood=self.likelihood,
            )
        #
        z = self.forward(inputs)
        pred_mean = self.mean_module(inputs) + z @ self.prediction_strategy.mean_cache
        precomputed_cache = self.prediction_strategy.covar_cache
        covar_inv_quad_form_root = z @ precomputed_cache

        pred_cov = MatmulLazyTensor(
            covar_inv_quad_form_root, covar_inv_quad_form_root.transpose(-1, -2)
        ).mul(self.likelihood.noise)

        return gpytorch.distributions.MultivariateNormal(pred_mean,
                                                         pred_cov.add_jitter())

    def forward(self, x):
        """Compute features at location x."""
        z = x @ self.w.transpose(-2, -1) + self.b
        return torch.cat([self.scale * torch.cos(z), self.scale * torch.sin(z)], dim=-1)
