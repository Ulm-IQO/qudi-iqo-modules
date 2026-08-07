# -*- coding: utf-8 -*-
"""
NI-DAQ APD Scanner Counter
===========================

Standalone Qudi hardware module that counts photon pulses from an APD or SPCM,
gated by an external TTL trigger (e.g. from the PI E-710 scanner).

This module has one job: given a trigger signal and a photon signal,
return an array of photon counts — one value per trigger window (pixel).

The PI E-710 fires n = round(t_pixel * 5000) trigger pulses per pixel.
This counter latches the cumulative photon count at each trigger edge,
then bins every n samples to produce one count per pixel.

Wiring (two BNC cables):
    PI E-710  Trigger OUT  →  NI  trigger_terminal  (e.g. PFI0)
    APD/SPCM  Signal OUT   →  NI  apd_terminal      (e.g. PFI8)

YAML configuration:
    hardware:
        my_counter:
            module.Class: 'hardware.ni_apd_scanner_counter.NIAPDScannerCounter'
            options:
                device_name:      'Dev1'
                counter_channel:  'ctr0'
                apd_terminal:     'PFI8'
                trigger_terminal: 'PFI0'
                channel_name:     'APD1'
                read_timeout:     120.0
"""

import numpy as np
import nidaqmx as ni
from nidaqmx.constants import Edge, AcquisitionType
from nidaqmx.stream_readers import CounterReader
from typing import Dict, List, Optional

from qudi.core.configoption import ConfigOption
from qudi.core.module import Base


# PI E-710 waveform generator sample rate — must match PIE710Controller.SAMP_RATE
_PI_SAMP_RATE = 5000.0


class NIAPDScannerCounter(Base):
    """
    NI-DAQ photon counter for triggered scanning acquisition.

    The PI E-710 fires n = round(t_pixel * 5000) TTL trigger pulses per pixel.
    The NI counter latches its cumulative photon count at each trigger edge.
    After the scan, read_scanner_counts() bins every n samples into one
    count per pixel.

    Example  (t_pixel = 0.6 ms, n = 3 pulses/pixel, 3 pixels)
    ──────────────────────────────────────────────────────────
    PI trigger:    ▲  ▲  ▲ │ ▲  ▲  ▲ │ ▲  ▲  ▲
    APD photons:   · · ···  ·    ·  ··    ···
    NI buffer:    [1, 2, 5,  6, 7, 9,  12, 15, 18]   (cumulative)
    after diff:   [1, 1, 3,  1, 1, 2,   3,  3,  3]   (per step)
    reshape(3,3): [[1,1,3], [1,1,2], [3,3,3]]
    row sums:     [5, 4, 9]                           (per pixel)
    """

    _device_name      = ConfigOption('device_name',      default='Dev1')
    _counter_channel  = ConfigOption('counter_channel',  default='ctr0')
    _apd_terminal     = ConfigOption('apd_terminal',     default='PFI8')
    _trigger_terminal = ConfigOption('trigger_terminal', default='PFI0')
    _channel_name     = ConfigOption('channel_name',     default='APD1')
    _read_timeout     = ConfigOption('read_timeout',     default=120.0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._task:              Optional[ni.Task]       = None
        self._reader:            Optional[CounterReader] = None
        self._n_steps_per_pixel: int                     = 1
        self._n_pixels:          int                     = 0

    # ══════════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def on_activate(self) -> None:
        """Verify the NI device is reachable."""
        dev_names = ni.system.System().devices.device_names
        if self._device_name.lower() not in {d.lower() for d in dev_names}:
            raise ValueError(
                f'NI device "{self._device_name}" not found. '
                f'Connected devices: {dev_names}'
            )
        # Normalise capitalisation
        for d in dev_names:
            if d.lower() == self._device_name.lower():
                self._device_name = d
                break

        self.log.info(
            f'NIAPDScannerCounter ready  '
            f'device={self._device_name}  '
            f'counter={self._counter_channel}  '
            f'APD={self._apd_terminal}  '
            f'trigger={self._trigger_terminal}  '
            f'channel="{self._channel_name}"'
        )

    def on_deactivate(self) -> None:
        """Release all NI resources."""
        self._cleanup_task()

    # ══════════════════════════════════════════════════════════════════════════
    # Properties  (read by interfuse to build ScanConstraints)
    # ══════════════════════════════════════════════════════════════════════════

    @property
    def channel_names(self) -> List[str]:
        """Channel names exposed to the interfuse and Qudi GUI."""
        return [self._channel_name]

    @property
    def channel_units(self) -> Dict[str, str]:
        """Units per channel. Interfuse divides raw counts by t_pixel → c/s."""
        return {self._channel_name: 'c/s'}

    # ══════════════════════════════════════════════════════════════════════════
    # Counting API  (called by interfuse)
    # ══════════════════════════════════════════════════════════════════════════

    def arm(self, n_pixels: int, t_pixel: float) -> None:
        """
        Build and start the NI counter task.

        Must be called BEFORE the scanner fires its first trigger pulse.
        Returns immediately — the task sits idle waiting for trigger edges.

        NI task setup
        ─────────────
        ci_count_edges_chan:
            Source = apd_terminal  (counts every photon edge, never resets)

        cfg_samp_clk_timing:
            Source = trigger_terminal  (PI E-710 Trigger OUT)
            Each rising edge latches the current cumulative photon count.
            Rate  = 5000 Hz  (PI waveform rate, not 1/t_pixel)
            Mode  = FINITE,  n_steps_per_pixel * n_pixels  samples total

        @param n_pixels : total pixels  (1D: n_x,  2D: n_x * n_y)
        @param t_pixel  : dwell time per pixel in seconds
        """
        self._cleanup_task()

        n = max(1, round(t_pixel * _PI_SAMP_RATE))   # trigger pulses per pixel
        n_samples_total = n * n_pixels

        self._n_steps_per_pixel = n
        self._n_pixels          = n_pixels

        self.log.debug(
            f'arm  n_pixels={n_pixels}  '
            f't_pixel={t_pixel * 1e3:.3f} ms  '
            f'n_steps/pixel={n}  '
            f'total NI samples={n_samples_total}'
        )

        try:
            self._task = ni.Task(f'APDScanCounter_{id(self):d}')

            # Count photon edges on apd_terminal
            ctr_name = f'/{self._device_name}/{self._counter_channel}'
            self._task.ci_channels.add_ci_count_edges_chan(
                ctr_name,
                edge=Edge.RISING,
            )
            self._task.ci_channels.all.ci_count_edges_term = (
                f'/{self._device_name}/{self._apd_terminal}'
            )

            # Latch cumulative count on each PI trigger pulse
            self._task.timing.cfg_samp_clk_timing(
                rate           = _PI_SAMP_RATE,
                source         = f'/{self._device_name}/{self._trigger_terminal}',
                active_edge    = Edge.RISING,
                sample_mode    = AcquisitionType.FINITE,
                samps_per_chan = n_samples_total,
            )

            self._reader = CounterReader(self._task.in_stream)
            self._reader.verify_array_shape = False

            self._task.start()

        except ni.DaqError as exc:
            self._cleanup_task()
            raise RuntimeError(f'NIAPDScannerCounter.arm failed: {exc}') from exc

    def read(self, n_pixels: int) -> Optional[Dict[str, np.ndarray]]:
        """
        Read and bin all samples into per-pixel counts.

        Blocks until all n_steps_per_pixel * n_pixels trigger pulses
        have been received and latched.

        Binning steps
        ─────────────
        1. Read  n_steps * n_pixels  cumulative values from NI buffer.
        2. Prepend 0 and np.diff  →  per-step photon increments.
        3. Reshape to  (n_pixels, n_steps).
        4. Sum each row  →  total counts per pixel.

        @param n_pixels : must match value passed to arm()
        @return         : {channel_name: np.ndarray(n_pixels,)} raw counts
                          or None on failure
        """
        if self._task is None or self._reader is None:
            self.log.error('read called but no counter task is active.')
            return None

        n               = self._n_steps_per_pixel
        n_samples_total = n * n_pixels

        try:
            raw = np.zeros(n_samples_total, dtype=np.float64)
            self._reader.read_many_sample_double(
                raw,
                number_of_samples_per_channel=n_samples_total,
                timeout=self._read_timeout,
            )

            # Cumulative → per-step increments → per-pixel totals
            increments = np.diff(np.concatenate([[0.0], raw]))
            counts     = increments.reshape(n_pixels, n).sum(axis=1)

            self.log.debug(
                f'read  n_pixels={n_pixels}  n_steps/pixel={n}  '
                f'total={int(counts.sum())}  '
                f'mean={counts.mean():.1f}  max={counts.max():.0f} cts/px'
            )

            return {self._channel_name: counts}

        except ni.DaqError as exc:
            self.log.error(f'NIAPDScannerCounter.read failed: {exc}')
            return None
        finally:
            self._cleanup_task()

    def stop(self) -> None:
        """
        Abort the counter immediately.

        Called on scan abort or emergency stop.
        Must never raise exceptions.
        """
        try:
            self._cleanup_task()
        except Exception as exc:
            self.log.warning(f'NIAPDScannerCounter.stop: {exc}')

    # ══════════════════════════════════════════════════════════════════════════
    # Internal
    # ══════════════════════════════════════════════════════════════════════════

    def _cleanup_task(self) -> None:
        self._reader = None
        if self._task is not None:
            try:
                if not self._task.is_task_done():
                    self._task.stop()
                self._task.close()
            except ni.DaqError as exc:
                self.log.warning(f'NI task cleanup: {exc}')
            finally:
                self._task = None