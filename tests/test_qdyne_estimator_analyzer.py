# -*- coding: utf-8 -*-
"""Regression tests for the qdyne state estimator and time trace analyzer.

Each test pins a bug that was live before this file existed.
"""
import logging

import numpy as np
import pytest

import qudi.logic.qdyne.qdyne_state_estimator as se
import qudi.logic.qdyne.qdyne_time_trace_analyzer as tta
from qudi.logic.qdyne.qdyne_state_estimator import (
    StateEstimator,
    StateEstimatorMain,
    StateEstimatorSettings,
)
from qudi.logic.qdyne.qdyne_time_trace_analyzer import (
    Analyzer,
    AnalyzerSettings,
    FourierAnalyzer,
    FourierAnalyzerSettings,
)
from qudi.logic.qdyne.tools.dataclass_tools import (
    get_subclass_dict,
    get_subclass_qualifier,
    get_subclasses,
)


def _advertised(settings_cls, module):
    """Method names the GUI offers, derived from the *settings* subclasses."""
    return sorted(get_subclass_dict(settings_cls.__module__, settings_cls))


def _implemented(base_cls, module):
    """Method names that actually have an implementation."""
    return sorted(
        get_subclass_qualifier(cls, base_cls) for cls in get_subclasses(module.__name__, base_cls)
    )


def test_every_advertised_estimator_method_has_an_implementation():
    """Settings classes and implementation classes are discovered by two separate scans, so an
    orphan on either side goes unnoticed. A TimeSeriesStateEstimatorSettings outlived its
    commented-out estimator, so the GUI offered 'TimeSeries' and selecting it raised KeyError deep
    inside configure_method()."""
    assert _advertised(StateEstimatorSettings, se) == _implemented(StateEstimator, se)


def test_every_advertised_analyzer_method_has_an_implementation():
    assert _advertised(AnalyzerSettings, tta) == _implemented(Analyzer, tta)


def test_setting_the_estimator_method_actually_configures_it():
    """StateEstimatorMain had no `method` property, so QdyneLogic.input_estimator_method()'s
    `self.estimator.method = ...` created a stray attribute and left `estimator` as None. Its
    sibling TimeTraceAnalyzerMain has always had the property - the two are meant to match."""
    main = StateEstimatorMain(logging.getLogger(__name__))
    assert main.estimator is None

    main.method = 'TimeTag'

    assert main.estimator is not None
    assert isinstance(main.estimator, StateEstimator)
    assert main.method == 'TimeTag'


def test_the_two_main_classes_present_the_same_method_interface():
    assert isinstance(getattr(StateEstimatorMain, 'method', None), property)
    assert isinstance(getattr(tta.TimeTraceAnalyzerMain, 'method', None), property)


def test_unknown_estimator_method_is_rejected_clearly():
    main = StateEstimatorMain(logging.getLogger(__name__))

    with pytest.raises(ValueError, match='No state estimator implementation'):
        main.method = 'NotAMethod'


def test_unknown_spectrum_type_is_rejected_at_construction():
    """Originally the unknown branch printed to stdout and fell through, leaving `spectrum` unbound
    so the return statement raised UnboundLocalError - an error that says nothing about the cause.
    It is now caught earlier still, when the settings object is built, so a bad value cannot reach
    the analyzer at all."""
    with pytest.raises(ValueError, match='spectrum_type must be one of'):
        FourierAnalyzerSettings(spectrum_type='typo')


def test_unknown_spectrum_type_also_raises_if_it_somehow_reaches_the_analyzer():
    """Defence in depth: the analyzer still refuses rather than falling through, in case a settings
    object is constructed by some path that bypasses validation."""

    class _Data:
        signal = [np.array([1.0, 2.0]), np.array([1.0, 2.0])]

    settings = FourierAnalyzerSettings()
    object.__setattr__(settings, 'spectrum_type', 'typo')   # bypass frozen + validation

    with pytest.raises(ValueError, match='Unsupported spectrum_type'):
        FourierAnalyzer().get_freq_domain_signal(_Data(), settings)


def _reference_photon_count(time_tag, start, stop):
    """The pre-refactor Average implementation, kept verbatim as the behavioural reference.

    The vectorised replacement must agree with this exactly on a well-formed stream - changing how
    photons are counted is a change to measured physics, not a refactor.
    """
    out, count = [], 0
    for i in range(1, len(time_tag)):
        if time_tag[i] != 0:
            if start <= time_tag[i] < stop:
                count += 1
        else:
            out.append(count)
            count = 0
    return np.array(out)


@pytest.mark.parametrize(
    'tags',
    [
        [0, 5, 7, 0, 9, 0],
        [0, 5, 7, 0, 9],
        [0, 0, 0],
        [0, 5, 5, 5, 0],
        [0, 4, 10, 0],          # both outside the [start, stop) window
    ],
)
def test_average_counting_is_unchanged_by_the_vectorisation(tags):
    estimator = se.TimeTagStateEstimator(logging.getLogger(__name__))

    result = estimator._photon_count(np.array(tags), 5, 10)

    assert result.tolist() == _reference_photon_count(np.array(tags), 5, 10).tolist()
    assert result.dtype == np.int64, 'unweighted counts must stay integers'


def test_both_count_modes_now_agree():
    """They used to disagree on the same data: one loop started at index 1 with `start <= tag`, the
    other at index 0 with `start < tag`, so Average and WeightedAverage gave different answers for
    identical input and unit weights."""
    estimator = se.TimeTagStateEstimator(logging.getLogger(__name__))
    tags = np.array([0, 5, 7, 0, 9, 0])

    plain = estimator._photon_count(tags, 5, 10)
    weighted = estimator._weighted_photon_count(tags, [1] * len(tags), 5, 10)

    assert plain.tolist() == weighted.tolist()


def test_weights_are_applied_per_photon():
    estimator = se.TimeTagStateEstimator(logging.getLogger(__name__))
    tags = np.array([0, 5, 7, 0, 9, 0])
    weights = [0, 10, 100, 0, 1000, 0]   # index-aligned with tags

    weighted = estimator._weighted_photon_count(tags, weights, 5, 10)

    assert weighted.tolist() == [110.0, 1000.0]


def test_empty_time_trace_does_not_crash_the_padding_calculation():
    """np.log2(0) is -inf and int(-inf) raises OverflowError."""
    assert FourierAnalyzer()._get_padded_time_trace_length(np.array([]), 1) == 0


@pytest.mark.parametrize('spectrum_type', ['amp', 'power'])
def test_known_spectrum_types_still_work(spectrum_type):
    class _Data:
        signal = [np.array([0.0, 1.0, 2.0]), np.array([3.0 + 0j, 1.0 + 0j, 0.5 + 0j])]

    freq, values = FourierAnalyzer().get_freq_domain_signal(
        _Data(), FourierAnalyzerSettings(spectrum_type=spectrum_type)
    )

    assert len(freq) == 3
    assert len(values) == 3
