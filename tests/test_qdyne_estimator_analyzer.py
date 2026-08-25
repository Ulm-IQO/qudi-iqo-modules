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


def test_unknown_spectrum_type_raises_instead_of_unbound_local():
    """The unknown branch printed to stdout and fell through, leaving `spectrum` unbound so the
    return statement raised UnboundLocalError - an error that says nothing about the real cause."""

    class _Data:
        signal = [np.array([1.0, 2.0]), np.array([1.0, 2.0])]

    with pytest.raises(ValueError, match='Unsupported spectrum_type'):
        FourierAnalyzer().get_freq_domain_signal(_Data(), FourierAnalyzerSettings(spectrum_type='typo'))


@pytest.mark.parametrize('spectrum_type', ['amp', 'power'])
def test_known_spectrum_types_still_work(spectrum_type):
    class _Data:
        signal = [np.array([0.0, 1.0, 2.0]), np.array([3.0 + 0j, 1.0 + 0j, 0.5 + 0j])]

    freq, values = FourierAnalyzer().get_freq_domain_signal(
        _Data(), FourierAnalyzerSettings(spectrum_type=spectrum_type)
    )

    assert len(freq) == 3
    assert len(values) == 3
