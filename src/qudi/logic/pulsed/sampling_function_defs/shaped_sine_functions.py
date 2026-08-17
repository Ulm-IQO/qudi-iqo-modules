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
