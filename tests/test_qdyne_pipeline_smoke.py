# -*- coding: utf-8 -*-
"""End-to-end smoke test for the qdyne measurement pipeline.

Drives the real QdyneCounterDummy through the real estimator and analyzer, in the same order
QdyneMeasurement.pull_data_and_estimate()/analyze_time_trace()/get_spectrum() do, without needing
qudi's module manager or a GUI. This is the regression signal for "does the logic still work at
all" - qdyne had no such test before.

The hardware module is built with __new__ and activated by hand: ConfigOption descriptors are only
resolved by the module manager, so the two the dummy reads are set directly on the instance.
"""
import logging

import numpy as np
import pytest

from qudi.hardware.dummy.qdyne_counter_dummy import QdyneCounterDummy
from qudi.interface.qdyne_counter_interface import GateMode
from qudi.logic.qdyne.qdyne_dataclass import MainDataClass
from qudi.logic.qdyne.qdyne_state_estimator import StateEstimatorMain, TimeTagStateEstimatorSettings
from qudi.logic.qdyne.qdyne_time_trace_analyzer import FourierAnalyzerSettings, TimeTraceAnalyzerMain


@pytest.fixture
def counter():
    hw = QdyneCounterDummy.__new__(QdyneCounterDummy)
    hw._measurements_per_data_poll = 100
    hw._max_number_bins = int(1e3)
    hw._sine_frequency_Hz = 200e6
    hw.on_activate()
    yield hw
    hw.on_deactivate()


def test_counter_configure_returns_the_four_applied_values(counter):
    """The interface contract the logic depends on - QdyneLogic.set_counter_settings() unpacks the
    result into four names, so a None or short tuple raises there."""
    applied = counter.configure(100e-9, 1e-6, GateMode.UNGATED, np.int64)

    assert len(applied) == 4
    bin_width, record_length, gate_mode, data_type = applied
    assert bin_width > 0
    assert record_length > 0
    assert isinstance(gate_mode, GateMode)
    assert isinstance(data_type, type)


def test_full_pipeline_raw_data_to_spectrum(counter):
    """raw data -> pulse histogram -> extract -> estimate -> analyze -> spectrum."""
    bin_width, record_length, _gate_mode, _dtype = counter.configure(
        100e-9, 1e-6, GateMode.UNGATED, np.int64
    )

    estimator = StateEstimatorMain(logging.getLogger(__name__))
    estimator.method = 'TimeTag'          # the assignment that used to be a silent no-op
    assert estimator.estimator is not None

    analyzer = TimeTraceAnalyzerMain()
    analyzer.method = 'Fourier'

    est_settings = TimeTagStateEstimatorSettings(
        bin_width=bin_width,
        record_length=record_length,
        sequence_length=record_length,
        sig_start=0.0,
        sig_end=record_length,
        count_mode='Average',
    )
    ana_settings = FourierAnalyzerSettings(sequence_length=record_length, spectrum_type='amp')

    data = MainDataClass()
    counter.start_measure()
    try:
        for _ in range(3):                # a few analysis ticks, as the timer would drive
            new_raw, info = counter.get_data()
            assert isinstance(info, dict)
            data.raw_data = np.append(data.raw_data, new_raw)

            pulse = estimator.get_pulse(data.raw_data, est_settings)
            assert len(pulse) == 2

            extracted = estimator.extract(new_raw, est_settings)
            data.time_trace = np.append(data.time_trace, estimator.estimate(extracted, est_settings))
    finally:
        counter.stop_measure()

    assert data.raw_data.size > 0
    assert data.time_trace.size > 0

    data.signal = analyzer.analyze(data, ana_settings)
    freq_domain = analyzer.get_freq_domain_signal(data, ana_settings)

    assert freq_domain.shape[0] == 2                    # [frequencies, values]
    assert freq_domain.shape[1] == len(data.signal[0])
    assert np.all(np.isfinite(freq_domain[1]))


def test_both_count_modes_run(counter):
    """WeightedAverage is reachable at all - its `weight` field is excluded from to_dict(), so it
    cannot currently be configured through the GUI or persisted (tracked as C3)."""
    bin_width, record_length, _gm, _dt = counter.configure(100e-9, 1e-6, GateMode.UNGATED, np.int64)
    estimator = StateEstimatorMain(logging.getLogger(__name__))
    estimator.method = 'TimeTag'

    counter.start_measure()
    try:
        raw, _info = counter.get_data()
    finally:
        counter.stop_measure()

    common = dict(bin_width=bin_width, record_length=record_length, sig_start=0.0, sig_end=record_length)
    plain = estimator.estimate(raw, TimeTagStateEstimatorSettings(count_mode='Average', **common))
    weighted = estimator.estimate(
        raw,
        TimeTagStateEstimatorSettings(
            count_mode='WeightedAverage', weight=[1] * len(raw), **common
        ),
    )

    assert plain.size > 0
    assert weighted.size > 0
