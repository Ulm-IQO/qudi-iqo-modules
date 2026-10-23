import numpy as np
from collections import OrderedDict
from qudi.logic.pulsed.sampling_functions import SamplingBase
from qudi.logic.pulsed.sampling_function_defs.basic_sampling_functions import (
    Sin,
    DoubleSinSum,
    TripleSinSum,
    QuintupleSinSum,
    SextupleSinSum,
)


class EnvelopeParabolaMixin(SamplingBase):
    """
    Mixin to sine like sampling functions that adds an envelope is a parabola of Nth order.
    To use, create a subclass inheritng the bare sine sampling function and this mixin.
    """

    params = OrderedDict()

    params['order'] = {'unit': '', 'init': 1, 'min': 0, 'max': 1000, 'type': int}

    def __init__(self, order=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.update(EnvelopeParabolaMixin.params)
        self.order = self.params['order']['init'] if 'order' not in kwargs else kwargs.pop('order')
        if order is None:
            self.order = self.params['order']['init']
        else:
            self.order = order

    def get_samples(self, time_array):
        bare_samples = super().get_samples(time_array)

        samples_arr = bare_samples * (
            1.0 - (2.0 * (np.arange(time_array.size) / time_array.size - 0.5)) ** (2 * self.order)
        )
        return samples_arr


class SinEnvelopeParabola(EnvelopeParabolaMixin, Sin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class DoubleSinSumEnvelopeParabola(EnvelopeParabolaMixin, DoubleSinSum):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class TripleSinSumEnvelopeParabola(EnvelopeParabolaMixin, TripleSinSum):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class EnvelopeSinnMixin(SamplingBase):
    """
    Mixin to sine like sampling functions that adds an envelope is a sin**n.
    To use, create a subclass inheritng the bare sine sampling function and this mixin.
    """

    params = OrderedDict()

    params['order'] = {'unit': '', 'init': 1, 'min': 0, 'max': 1000, 'type': float}

    def __init__(self, order=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.update(EnvelopeSinnMixin.params)
        if order is None:
            self.order = self.params['order']['init']
        else:
            self.order = order

    def get_samples(self, time_array):
        bare_samples = super().get_samples(time_array)
        t_rel = np.arange(time_array.size) / time_array.size  # time in units from 0..1
        samples_arr = bare_samples * np.sin(np.pi * t_rel) ** self.order
        return samples_arr


class SinEnvelopeSinn(EnvelopeSinnMixin, Sin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class DoubleSinSumEnvelopeSinn(EnvelopeSinnMixin, DoubleSinSum):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class TripleSinSumEnvelopeSinn(EnvelopeSinnMixin, TripleSinSum):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class QuintupleSinSumEnvelopeSinn(EnvelopeSinnMixin, QuintupleSinSum):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class SextupleSinSumEnvelopeSinn(EnvelopeSinnMixin, SextupleSinSum):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class EnvelopeGaussianMixin(SamplingBase):
    """
    Mixin to sine like sampling functions that adds a Gaussian envelope.
    The envelope is centred in the element and truncated at +-n_sigma, so sigma = length / (2 * n_sigma).
    With `lifted` the value at the truncation point is subtracted and the result renormalised, so the
    envelope starts and ends at exactly zero instead of stepping by exp(-n_sigma**2 / 2) at each edge.
    To use, create a subclass inheriting the bare sine sampling function and this mixin.
    """

    params = OrderedDict()

    params['n_sigma'] = {'unit': '', 'init': 2.0, 'min': 0.5, 'max': 10.0, 'type': float}
    params['lifted'] = {'unit': '', 'init': True, 'min': 0, 'max': 1, 'type': bool}

    def __init__(self, n_sigma=None, lifted=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.update(EnvelopeGaussianMixin.params)
        self.n_sigma = self.params['n_sigma']['init'] if n_sigma is None else float(n_sigma)
        self.lifted = self.params['lifted']['init'] if lifted is None else bool(lifted)

    @staticmethod
    def envelope(t_rel, n_sigma, lifted):
        """
        Gaussian envelope on the relative time t_rel in [0, 1] with its peak at 0.5.

        @param array_like t_rel: relative time within the element
        @param float n_sigma: truncation point in units of sigma
        @param bool lifted: subtract the edge value so that the envelope starts and ends at zero
        @return numpy.ndarray: envelope values in [0, 1]
        """
        envelope = np.exp(-0.5 * ((np.asarray(t_rel, dtype=float) - 0.5) * 2.0 * n_sigma) ** 2)
        if lifted:
            edge = np.exp(-0.5 * n_sigma**2)
            envelope = np.clip((envelope - edge) / (1.0 - edge), 0.0, None)
        return envelope

    def get_samples(self, time_array):
        bare_samples = super().get_samples(time_array)
        t_rel = np.arange(time_array.size) / time_array.size  # time in units from 0..1
        return bare_samples * self.envelope(t_rel, self.n_sigma, self.lifted)


class SinEnvelopeGaussian(EnvelopeGaussianMixin, Sin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class DoubleSinSumEnvelopeGaussian(EnvelopeGaussianMixin, DoubleSinSum):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class TripleSinSumEnvelopeGaussian(EnvelopeGaussianMixin, TripleSinSum):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
