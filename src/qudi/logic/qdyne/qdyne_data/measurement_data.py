# -*- coding: utf-8 -*-
"""Data containers for a qdyne measurement.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-iqo-modules/>

This file is part of qudi.

Qudi is free software: you can redistribute it and/or modify it under the terms of
the GNU Lesser General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version.

Qudi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along with qudi.
If not, see <https://www.gnu.org/licenses/>.
"""
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Optional

import numpy as np
from scipy import signal as scipy_signal

from qudi.core.logger import get_logger
from qudi.logic.qdyne.qdyne_data.growable_array import GrowableArray

__all__ = ['FreqDomainData', 'MeasurementChunk', 'QDyneMetadata', 'MainDataClass']

_logger = get_logger(__name__)

#: The data products a qdyne measurement produces and can save. Defined once, here, because it used
#: to be duplicated as a class attribute on both DataManagerSettings and QdyneDataManager - two
#: independent lists that had to agree.
DATA_TYPES = ('raw_data', 'time_trace', 'freq_domain', 'time_domain')


@dataclass
class QDyneMetadata:
    """Everything needed to say how a saved measurement was produced.

    Still dict-valued rather than holding the settings dataclasses directly: the estimator and
    analyzer settings classes vary by method, and the data-storage layer writes plain dicts. The
    dicts are produced by the settings objects' own to_dict() and consumed by their from_dict(), so
    the typed containers remain the source of truth on both sides of the file boundary - see
    QdyneLogic._restore_loaded_mode().
    """

    generation_parameters: dict = field(default_factory=dict)
    #: The predefined generate method that produced the loaded waveform, and the arguments it was
    #: actually called with. Recorded by QdyneMeasurement from what MeasurementGenerator observed at
    #: generation time - NOT looked up from `generate_method_params`, which holds each method's
    #: static signature defaults and would silently record values the measurement never used. Both
    #: stay empty unless the loaded asset is provably the one that was generated.
    generation_method: str = ''
    generation_method_parameters: dict = field(default_factory=dict)
    counter_settings: dict = field(default_factory=dict)
    measurement_settings: dict = field(default_factory=dict)
    state_estimation_method: str = ''
    state_estimation_mode: str = ''
    state_estimation_settings: dict = field(default_factory=dict)
    analysis_method: str = ''
    analysis_mode: str = ''
    analysis_settings: dict = field(default_factory=dict)
    #: Provenance. A saved qdyne measurement previously carried no record of when it ran, how much
    #: of it ran, or on what instrument.
    start_time: str = ''
    elapsed_sweeps: int = 0
    elapsed_time: float = 0.0
    counter_hardware: str = ''

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Any) -> 'QDyneMetadata':
        """Tolerant of a file written by a different version - unknown keys are dropped with a
        warning and missing ones take their defaults, rather than raising TypeError out of
        __init__ and leaving the caller with stale metadata."""
        if not isinstance(data, dict):
            return cls()
        valid = {f.name for f in fields(cls)}
        unknown = set(data) - valid
        if unknown:
            _logger.warning(
                f'Ignoring saved metadata key(s) {sorted(unknown)} - not field(s) of {cls.__name__}.'
            )
        return cls(**{k: v for k, v in data.items() if k in valid})


class FreqDomainData:
    """The spectrum, plus the peak-picking knobs the GUI drives.

    Those knobs (`current_peak`, `range_index`, `peak_threshold`, `peak_separation`) are really view
    state rather than measurement data, and would sit better in a settings container. They are left
    here for now because the analysis widget reads *and writes* them directly; moving them is GUI
    work and buys layering cleanliness rather than correctness.
    """

    def __init__(self):
        self.x = None
        self.y = None
        self.peaks = []
        self.current_peak = 0
        self.range_index = 10
        self.peak_threshold = 10.0
        self.peak_separation = 10

    def get_peaks(self):
        """Find the peaks of the non-negative frequency domain signal."""
        if self.y is None or len(self.y) < 2:
            self.peaks = []
            return
        height = self.peak_threshold * np.mean(self.y[1:])
        all_peaks = scipy_signal.find_peaks(self.y, height=height)[0]
        if len(all_peaks) > 0:
            self.peaks = [all_peaks[0]]
            for peak in all_peaks[1:]:
                if peak - self.peaks[-1] >= self.peak_separation:
                    self.peaks.append(peak)
        else:
            self.peaks = [int(np.argmax(self.y))]

    @property
    def data_around_peak(self):
        start_index = max(0, self.current_peak - self.range_index)
        end_index = min(
            self.x.size,
            self.current_peak + self.range_index + 1,  # +1 because slicing is end exclusive
        )
        return [self.x[start_index:end_index], self.y[start_index:end_index]]


@dataclass
class MeasurementChunk:
    """One poll's worth of data, on its way through the pipeline.

    Distinct from the accumulated MainDataClass on purpose. Both roles used to be filled by the same
    type, so the per-tick object carried a `freq_domain`, a `metadata` and a `freq_data` that never
    meant anything, and it was not obvious at a call site which of the two you were holding.
    """

    raw_data: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int64))
    extracted_data: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int64))
    time_trace: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int64))
    #: Straight from QdyneCounterInterface.get_data()'s info_dict - elapsed_sweeps / elapsed_time.
    #: These were previously discarded at the call site and the corresponding attributes never
    #: updated.
    info: Dict[str, Any] = field(default_factory=dict)

    def clear(self) -> None:
        empty = np.array([], dtype=np.int64)
        self.raw_data = empty
        self.extracted_data = empty
        self.time_trace = empty
        self.info = {}


class MainDataClass:
    """The accumulated data of one qdyne measurement.

    `raw_data`, `extracted_data` and `time_trace` are backed by GrowableArray, so the measurement
    loop appends in amortised constant time instead of reallocating the whole history on every tick.
    They still read as ordinary numpy arrays through the properties below, so storage, analysis and
    the GUI are unaffected.

    Use `append_*` on the hot path. Assigning to the property still works and replaces the contents
    outright, which is what loading a saved measurement needs.

    A plain class rather than a dataclass: this is live, mutating measurement state with computed
    properties over private buffers, which is not what a dataclass is for.
    """

    def __init__(self):
        self._raw_data = GrowableArray(dtype=np.int64)
        self._extracted_data = GrowableArray(dtype=np.int64)
        self._time_trace = GrowableArray(dtype=np.int64)

        self.pulse_data = np.array([])
        self.signal = np.array([])
        self.freq_domain = np.array([])
        self.time_domain = np.array([])
        self.freq_data = FreqDomainData()
        self.metadata = QDyneMetadata()

        #: Result of the last fit. Declared here rather than conjured onto the instance by
        #: QdyneLogic.do_fit(), which meant reset() could not clear them - so a fit from the
        #: previous measurement survived into the next one and could be shown against it.
        self.fit_config: str = ''
        self.fit_result = None

        #: The time-domain plot fits independently of the spectrum, so it keeps its own result.
        #: Sharing one pair meant a fit on either plot overwrote the other's curve.
        self.time_fit_config: str = ''
        self.time_fit_result = None

        #: Running pulse histogram, accumulated rather than recomputed - see add_pulse_counts().
        self._pulse_hist: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ accumulated arrays

    @property
    def raw_data(self) -> np.ndarray:
        return self._raw_data.view

    @raw_data.setter
    def raw_data(self, values) -> None:
        self._raw_data.replace(values)

    @property
    def extracted_data(self) -> np.ndarray:
        return self._extracted_data.view

    @extracted_data.setter
    def extracted_data(self, values) -> None:
        self._extracted_data.replace(values)

    @property
    def time_trace(self) -> np.ndarray:
        return self._time_trace.view

    @time_trace.setter
    def time_trace(self, values) -> None:
        self._time_trace.replace(values)

    def append_raw_data(self, chunk) -> None:
        self._raw_data.append(chunk)

    def append_extracted_data(self, chunk) -> None:
        self._extracted_data.append(chunk)

    def append_time_trace(self, chunk) -> None:
        self._time_trace.append(chunk)

    # ------------------------------------------------------------------ pulse histogram

    def add_pulse_counts(self, counts: np.ndarray) -> np.ndarray:
        """Add one chunk's histogram to the running total and return the total.

        A histogram is additive, so accumulating per-chunk counts is exactly equivalent to
        re-histogramming the whole stream - and costs O(new samples) instead of O(all samples). The
        pulse view used to be rebuilt from the entire accumulated raw data on every analysis tick,
        which made it quadratic over a run, independently of the append cost.

        Call reset_pulse_counts() whenever the binning changes, since old bins no longer mean the
        same thing.
        """
        counts = np.asarray(counts)
        if self._pulse_hist is not None and self._pulse_hist.shape != counts.shape:
            # A shape change means the binning moved without anyone calling reset_pulse_counts(),
            # so the accumulated history is silently thrown away. That is the right thing to do with
            # bins that no longer mean the same thing, but it must not happen quietly - losing a
            # run's pulse histogram with no message is indistinguishable from the view being broken.
            _logger.warning(
                f'Pulse histogram shape changed from {self._pulse_hist.shape} to {counts.shape} - '
                f'the accumulated histogram has been discarded. This usually means the estimator '
                f'binning changed without reset_pulse_counts() being called.'
            )
            self._pulse_hist = None
        if self._pulse_hist is None:
            self._pulse_hist = counts.astype(np.int64, copy=True)
        else:
            self._pulse_hist += counts
        return self._pulse_hist

    def reset_pulse_counts(self) -> None:
        self._pulse_hist = None

    @property
    def pulse_counts(self) -> Optional[np.ndarray]:
        return self._pulse_hist

    # ------------------------------------------------------------------ lifecycle

    @property
    def data_types(self) -> tuple:
        """The saveable data products - see DATA_TYPES.

        Replaces a `data_list` property that walked dir(self.__class__) and called getattr on every
        name it found, including itself, which recursed until Python gave up. Nothing called it, so
        the RecursionError was latent.
        """
        return DATA_TYPES

    def reset(self) -> None:
        """Start a fresh measurement, keeping the allocated capacity."""
        self._raw_data.clear()
        self._extracted_data.clear()
        self._time_trace.clear()
        self.reset_pulse_counts()

        self.pulse_data = np.array([])
        self.signal = np.array([])
        self.freq_domain = np.array([])
        self.time_domain = np.array([])
        self.freq_data = FreqDomainData()
        self.metadata = QDyneMetadata()
        self.fit_config = ''
        self.fit_result = None
        self.time_fit_config = ''
        self.time_fit_result = None
