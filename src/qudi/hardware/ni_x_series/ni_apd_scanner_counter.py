# -*- coding: utf-8 -*-
"""
NI-DAQ APD Scanner Counter
===========================

Standalone Qudi hardware module that counts photon pulses from an APD or SPCM,
synchronised to the PI E-710 scanner gate signal.

Why two NI counter tasks?
-------------------------
NI CI (counter input) tasks do NOT reliably support:
  - External sample clock from PFI terminals  (device-dependent, fails here)
  - Start triggers                             (device-dependent, failed earlier)

NI CO (counter output) tasks DO support start triggers on all X-Series devices.

Solution
--------
CO task  (clock_counter, default ctr1):
    Generates exactly  n_pixels * n_steps + 1  pulses at 5000 Hz.
    Starts when the PI gate signal (trigger_terminal) goes HIGH.

CI task  (counter_channel, default ctr0):
    Counts APD photon edges.
    Clocked by the CO task's internal output (Ctr1InternalOutput).
    FINITE mode: collects exactly  n_pixels * n_steps + 1  samples.

Why n_pixels * n_steps + 1 samples?
------------------------------------
raw[0]  = cumulative photon count the instant gate goes HIGH = pre-scan background
raw[k]  = cumulative count k steps into the scan

np.diff(raw) removes the background automatically:
    diff[0] = raw[1] - raw[0] = photons during scan step 0
    diff[k] = photons during scan step k

reshape(n_pixels, n_steps).sum(axis=1) gives per-pixel counts.

Wiring (two BNC cables):
    PI E-710  Trigger OUT  ->  NI  trigger_terminal  (e.g. PFI1)
    APD/SPCM  Signal OUT   ->  NI  apd_terminal      (e.g. PFI8)

YAML configuration:
    hardware:
        my_counter:
            module.Class: 'hardware.ni_apd_scanner_counter.NIAPDScannerCounter'
            options:
                device_name:      'Dev1'
                counter_channel:  'ctr0'
                clock_counter:    'ctr1'
                apd_terminal:     'PFI8'
                trigger_terminal: 'PFI1'
                channel_name:     'APD1'
                read_timeout:     30.0
"""

import numpy as np
import nidaqmx as ni
from nidaqmx.constants import Edge, AcquisitionType, Level
from nidaqmx.stream_readers import CounterReader
from typing import Dict, List, Optional

from qudi.core.configoption import ConfigOption
from qudi.core.module import Base


# PI E-710 waveform generator sample rate (Hz)
_PI_SAMP_RATE: float = 5000.0


class NIAPDScannerCounter(Base):
    """
    NI-DAQ photon counter using CO+CI dual-task architecture.

    CO task triggered by PI gate --> CI task clocked by CO output.

    Three methods consumed by PIE710CounterInterfuse:
        arm(n_pixels, t_pixel)   -- create and start both tasks
        read(n_pixels)           -- wait for CO to finish, return per-pixel counts
        stop()                   -- abort both tasks, never raises
    """

    _device_name      = ConfigOption('device_name',      default='Dev1')
    _counter_channel  = ConfigOption('counter_channel',  default='ctr0')
    _clock_counter    = ConfigOption('clock_counter',    default='ctr1')
    _apd_terminal     = ConfigOption('apd_terminal',     default='PFI8')
    _trigger_terminal = ConfigOption('trigger_terminal', default='PFI1')
    _channel_name     = ConfigOption('channel_name',     default='APD1')
    _read_timeout     = ConfigOption('read_timeout',     default=30.0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._task:              Optional[ni.Task]       = None   # CI task
        self._co_task:           Optional[ni.Task]       = None   # CO clock task
        self._reader:            Optional[CounterReader] = None
        self._n_steps_per_pixel: int                     = 1
        self._n_pixels:          int                     = 0

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def on_activate(self) -> None:
        """Verify NI device is reachable and log configuration."""
        dev_names = ni.system.System().devices.device_names
        if self._device_name.lower() not in {d.lower() for d in dev_names}:
            raise ValueError(
                f'NI device "{self._device_name}" not found. '
                f'Connected devices: {dev_names}'
            )
        for d in dev_names:
            if d.lower() == self._device_name.lower():
                self._device_name = d
                break

        # Warn if user accidentally uses same counter for CI and CO
        if self._counter_channel.lower() == self._clock_counter.lower():
            raise ValueError(
                f'counter_channel and clock_counter must be different. '
                f'Both are set to "{self._counter_channel}".'
            )

        clock_num = ''.join(filter(str.isdigit, self._clock_counter))
        co_output = f'/{self._device_name}/Ctr{clock_num}InternalOutput'

        self.log.info(
            f'NIAPDScannerCounter ready -- '
            f'device={self._device_name}  '
            f'CI counter={self._counter_channel}  '
            f'CO counter={self._clock_counter}  '
            f'CO output routed to: {co_output}  '
            f'APD terminal={self._apd_terminal}  '
            f'gate terminal={self._trigger_terminal}  '
            f'channel="{self._channel_name}"'
        )

    def on_deactivate(self) -> None:
        self._cleanup_task()

    # =========================================================================
    # Properties  (read by interfuse to build ScanConstraints)
    # =========================================================================

    @property
    def channel_names(self) -> List[str]:
        return [self._channel_name]

    @property
    def channel_units(self) -> Dict[str, str]:
        """Interfuse divides raw counts by t_pixel to produce c/s."""
        return {self._channel_name: 'c/s'}

    # =========================================================================
    # Counting API
    # =========================================================================

    def arm(self, n_pixels: int, t_pixel: float) -> None:
        """
        Create and start the CO + CI task pair.

        Call BEFORE sending the PI scan command.
        Both tasks start and wait silently:
          CO task: waits for gate RISING edge on trigger_terminal
          CI task: waits for CO to provide first clock edge

        Task layout
        -----------
        CO (clock_counter, e.g. ctr1):
            Finite pulse train at 5000 Hz.
            Generates n_pixels * n_steps + 1 pulses.
            Starts when gate RISING edge arrives on trigger_terminal.
            Duration after trigger: (n_pixels * n_steps + 1) / 5000 seconds.

        CI (counter_channel, e.g. ctr0):
            Counts rising edges on apd_terminal (photon pulses).
            Clocked by Ctr{N}InternalOutput (CO task's generated clock).
            Finite: collects n_pixels * n_steps + 1 cumulative values.

        Why n_pixels * n_steps + 1?
            raw[0] = background baseline (cumulative count at gate HIGH)
            np.diff(raw) = n_pixels * n_steps per-step increments, background-free

        @param n_pixels : pixels per sweep (1D: n_x, 2D: one fast-axis line)
        @param t_pixel  : dwell time per pixel in seconds (= 1/frequency)
        """
        self._cleanup_task()

        n = max(1, round(t_pixel * _PI_SAMP_RATE))   # waveform steps per pixel
        n_collect = n * n_pixels + 1                  # +1 for diff baseline

        self._n_steps_per_pixel = n
        self._n_pixels          = n_pixels

        # Derive CO output terminal for routing to CI sample clock
        clock_num = ''.join(filter(str.isdigit, self._clock_counter))
        co_output = f'/{self._device_name}/Ctr{clock_num}InternalOutput'

        self.log.debug(
            f'arm  n_pixels={n_pixels}  '
            f't_pixel={t_pixel * 1e3:.3f} ms  '
            f'steps/pixel={n}  '
            f'n_collect={n_collect}  '
            f'CO clock -> {co_output}'
        )

        try:
            # ---- CO task: finite pulse train, triggered by gate ------------
            self._co_task = ni.Task('ScanClock')
            self._co_task.co_channels.add_co_pulse_chan_freq(
                counter      = f'/{self._device_name}/{self._clock_counter}',
                freq         = _PI_SAMP_RATE,
                duty_cycle   = 0.5,
                idle_state   = Level.LOW,
                initial_delay= 0.0,
            )
            self._co_task.timing.cfg_implicit_timing(
                sample_mode  = AcquisitionType.FINITE,
                samps_per_chan= n_collect,
            )
            # Gate RISING edge starts the CO pulse train.
            # CO tasks support start triggers on all NI X-Series devices.
            self._co_task.triggers.start_trigger.cfg_dig_edge_start_trig(
                trigger_source = f'/{self._device_name}/{self._trigger_terminal}',
                trigger_edge   = Edge.RISING,
            )

            # ---- CI task: count photons, clocked by CO output --------------
            self._task = ni.Task('APDScanCounter')
            self._task.ci_channels.add_ci_count_edges_chan(
                f'/{self._device_name}/{self._counter_channel}',
                edge=Edge.RISING,
            )
            # Route APD signal to counter source input
            self._task.ci_channels.all.ci_count_edges_term = (
                f'/{self._device_name}/{self._apd_terminal}'
            )
            # Sample clock from CO internal output -- always works via internal routing
            self._task.timing.cfg_samp_clk_timing(
                rate           = _PI_SAMP_RATE,
                source         = co_output,
                active_edge    = Edge.RISING,
                sample_mode    = AcquisitionType.FINITE,
                samps_per_chan = n_collect,
            )

            self._reader = CounterReader(self._task.in_stream)
            self._reader.verify_array_shape = False

            # Start CI first -- it immediately waits for the CO to provide a clock
            self._task.start()
            # Start CO -- it waits for the gate trigger on trigger_terminal
            self._co_task.start()

        except ni.DaqError as exc:
            self._cleanup_task()
            raise RuntimeError(f'NIAPDScannerCounter.arm failed: {exc}') from exc

    def read(self, n_pixels: int) -> Optional[Dict[str, np.ndarray]]:
        """
        Wait for the CO task to finish, then read and return per-pixel counts.

        The CO task generates its last pulse (n_collect/5000) seconds after
        the gate went HIGH. By the time the interfuse calls read() (after
        wait_for_scan_complete confirms the PI generators are idle), the CO
        and CI tasks are already done. wait_until_done returns immediately.

        Data processing
        ---------------
        raw[k]  = cumulative photon count at the end of CO clock pulse k
        raw[0]  = background accumulated before the gate went HIGH (baseline)

        np.diff(raw):
            diff[0] = raw[1] - raw[0] = photons during scan step 0
            diff[k] = photons during scan step k
            Background cancels exactly because raw[0] is subtracted.

        reshape(n_pixels, n_steps).sum(axis=1):
            Groups n_steps per pixel and sums to get total counts per pixel.

        @param n_pixels : must match value passed to arm()
        @return         : {channel_name: np.ndarray(n_pixels,)} raw counts
                          or None on failure
        """
        if self._task is None or self._co_task is None or self._reader is None:
            self.log.error('read() called without active tasks.')
            return None

        n        = self._n_steps_per_pixel
        n_collect = n * n_pixels + 1

        try:
            # Wait for CO to finish generating all n_collect pulses.
            # This blocks until the scan's gate HIGH region ends.
            self._co_task.wait_until_done(timeout=self._read_timeout)

            # CI task is clocked by CO and collects the same number of samples.
            self._task.wait_until_done(timeout=10.0)

            # Read all cumulative values from NI buffer
            raw = np.zeros(n_collect, dtype=np.float64)
            self._reader.read_many_sample_double(
                raw,
                number_of_samples_per_channel=n_collect,
                timeout=10.0,
            )

        except ni.DaqError as exc:
            self.log.error(
                f'NIAPDScannerCounter.read failed: {exc}\n'
                f'Checklist:\n'
                f'  1. BNC cable: PI Trigger OUT -> NI {self._trigger_terminal}\n'
                f'  2. Gate signal must go HIGH at start of scan region\n'
                f'  3. Expected gate: HIGH for {n_pixels * t_pixel * 1e3:.0f} ms\n'
                f'     where t_pixel = 1/frequency\n'
                f'  4. CO counter ({self._clock_counter}) and CI counter '
                f'({self._counter_channel}) must be different'
            )
            return None
        finally:
            self._cleanup_task()

        # np.diff removes the background baseline (raw[0])
        # Result: n_pixels * n_steps per-step photon increments
        increments = np.diff(raw)
        counts     = increments.reshape(n_pixels, n).sum(axis=1)

        self.log.debug(
            f'read OK  n_pixels={n_pixels}  steps/pixel={n}  '
            f'total={int(counts.sum())}  '
            f'mean={counts.mean():.1f}  max={counts.max():.0f} cts/px'
        )

        return {self._channel_name: counts}

    def stop(self) -> None:
        """Abort both tasks immediately. Must never raise exceptions."""
        try:
            self._cleanup_task()
        except Exception as exc:
            self.log.warning(f'NIAPDScannerCounter.stop: {exc}')

    # =========================================================================
    # Internal
    # =========================================================================

    def _cleanup_task(self) -> None:
        """Stop and close both NI tasks. Safe to call at any time."""
        self._reader = None
        for attr in ('_task', '_co_task'):
            task = getattr(self, attr, None)
            if task is not None:
                try:
                    if not task.is_task_done():
                        task.stop()
                    task.close()
                except ni.DaqError as exc:
                    self.log.warning(f'NI task cleanup ({attr}): {exc}')
                finally:
                    setattr(self, attr, None)