# -*- coding: utf-8 -*-
"""
NI-DAQ APD Scanner Counter
===========================

Standalone Qudi hardware module that counts photon pulses from an APD or SPCM,
synchronised to the PI E-710 scanner gate signal.

Conflict handling
-----------------
NI counter resources can be held by other Qudi modules (e.g. a slow counter
or NIXSeriesInStreamer which internally creates a CO clock task).

On arm(), conflicts are resolved in this order:

  1. Module-level registry
     Any tasks created by a previous NIAPDScannerCounter instance that were
     not properly cleaned up are stopped and removed from the registry.
     This handles zombie tasks from crashed or improperly deactivated instances.

  2. Task creation attempt
     The CO and CI tasks are created. If NI returns -50103 (resource reserved),
     the conflict is handled based on reset_device_on_conflict:

     reset_device_on_conflict: false  (default)
         Raise a RuntimeError with a clear message telling the user which
         Qudi module to deactivate. No other modules are affected.

     reset_device_on_conflict: true
         Call device.reset_device() which forcibly stops ALL tasks on the
         NI card, then retry task creation.
         WARNING: this will interrupt any other running NI task on the device
         (slow counters, analog outputs, etc). Choose this if you want
         seamless switching between a time-series counter and scanning.

Why the previous probe approach failed
--------------------------------------
The probe tested each counter channel by creating a CI task on it.
However the NIXSeriesInStreamer internally creates a CO (output) task on
one of the counters for its sample clock. A CI probe does not detect a CO
reservation, so the probe reported "free" while the channel was actually held.
The try/catch approach is reliable because it uses the exact same task type
(CO for clock, CI for counting) that the actual arm() will create.

Task design (CO + CI)
---------------------
CO task (clock_counter, default ctr1):
    Finite pulse train at 5000 Hz.
    Starts when PI gate signal (trigger_terminal) goes HIGH.
    Generates n_pixels * n_steps + 1 pulses.

CI task (counter_channel, default ctr0):
    Counts APD photon rising edges (cumulative).
    Clocked by Ctr{N}InternalOutput from the CO task.
    Collects n_pixels * n_steps + 1 samples.

Processing:
    np.diff(raw) -> per-step photon increments (background-subtracted)
    reshape(n_pixels, n_steps).sum(axis=1) -> per-pixel counts

Wiring (two BNC cables):
    PI E-710  Trigger OUT  ->  NI  trigger_terminal  (e.g. PFI1)
    APD/SPCM  Signal OUT   ->  NI  apd_terminal      (e.g. PFI8)

YAML configuration:
    hardware:
        my_counter:
            module.Class: 'hardware.ni_apd_scanner_counter.NIAPDScannerCounter'
            options:
                device_name:               'Dev1'
                counter_channel:           'ctr0'
                clock_counter:             'ctr1'
                apd_terminal:              'PFI8'
                trigger_terminal:          'PFI1'
                channel_name:              'APD1'
                read_timeout:              30.0
                reset_device_on_conflict:  false
"""

import threading
import numpy as np
import nidaqmx as ni
from nidaqmx.constants import Edge, AcquisitionType, Level
from nidaqmx.stream_readers import CounterReader
from typing import Dict, List, Optional, Tuple

from qudi.core.configoption import ConfigOption
from qudi.core.module import Base


# PI E-710 waveform generator sample rate (Hz)
_PI_SAMP_RATE: float = 5000.0

# =============================================================================
# Module-level task registry
# =============================================================================
# Maps (device_name_lower, channel_name_lower) -> ni.Task
# Allows any NIAPDScannerCounter instance to find and stop zombie tasks
# left by a previous crashed/improperly deactivated instance of this class.
# Only tracks tasks created by NIAPDScannerCounter -- not tasks from other modules.
# =============================================================================
_TASK_REGISTRY: Dict[Tuple[str, str], ni.Task] = {}
_REGISTRY_LOCK = threading.Lock()


class NIAPDScannerCounter(Base):
    """
    NI-DAQ photon counter for PI E-710 triggered scanning.

    CO (ctr1): generates 5 kHz clock, starts on PI gate rising edge.
    CI (ctr0): counts APD photons, clocked by CO internal output.

    Three methods consumed by PIE710CounterInterfuse:
        arm(n_pixels, t_pixel)   -- resolve conflicts, create tasks, start
        read(n_pixels)           -- wait for CO to finish, return per-pixel counts
        stop()                   -- abort both tasks, never raises
    """

    _device_name       = ConfigOption('device_name',              default='Dev1')
    _counter_channel   = ConfigOption('counter_channel',          default='ctr0')
    _clock_counter     = ConfigOption('clock_counter',            default='ctr1')
    _apd_terminal      = ConfigOption('apd_terminal',             default='PFI8')
    _trigger_terminal  = ConfigOption('trigger_terminal',         default='PFI1')
    _channel_name      = ConfigOption('channel_name',             default='APD1')
    _read_timeout      = ConfigOption('read_timeout',             default=30.0)
    _reset_on_conflict = ConfigOption('reset_device_on_conflict', default=False)

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
        """Verify NI device is reachable."""
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

        if self._counter_channel.lower() == self._clock_counter.lower():
            raise ValueError(
                f'counter_channel and clock_counter must be different. '
                f'Both are set to "{self._counter_channel}".'
            )

        clock_num = ''.join(filter(str.isdigit, self._clock_counter))

        self.log.info(
            f'NIAPDScannerCounter ready -- '
            f'device={self._device_name}  '
            f'CI counter={self._counter_channel}  '
            f'CO counter={self._clock_counter}  '
            f'CO output=Ctr{clock_num}InternalOutput  '
            f'APD terminal={self._apd_terminal}  '
            f'gate terminal={self._trigger_terminal}  '
            f'channel="{self._channel_name}"  '
            f'reset_on_conflict={self._reset_on_conflict}'
        )

    def on_deactivate(self) -> None:
        """Stop tasks and remove from registry."""
        self._cleanup_task()

    # =========================================================================
    # Properties  (read by interfuse to build ScanConstraints)
    # =========================================================================

    @property
    def channel_names(self) -> List[str]:
        return [self._channel_name]

    @property
    def channel_units(self) -> Dict[str, str]:
        return {self._channel_name: 'c/s'}

    # =========================================================================
    # Counting API
    # =========================================================================

    def arm(self, n_pixels: int, t_pixel: float) -> None:
        """
        Resolve counter conflicts then create and start CO + CI tasks.

        Conflict resolution
        -------------------
        1. Clean up our own previous tasks.
        2. Stop zombie tasks in the module-level registry using our channels.
        3. Try to create the CO and CI tasks.
           If -50103 (resource reserved) is raised during CO or CI creation:
             - reset_device_on_conflict=True:
                 device.reset_device() frees all NI tasks, then retry once.
             - reset_device_on_conflict=False:
                 raise RuntimeError with instructions.

        @param n_pixels : total pixels  (1D: n_x,  2D: one fast-axis line)
        @param t_pixel  : dwell time per pixel in seconds
        """
        # Step 1: clean up our own previous tasks
        self._cleanup_task()

        # Step 2: stop any zombie tasks from previous instances of this class
        self._stop_registered_tasks()

        # Step 3: try to create tasks, handle -50103 if it occurs
        n         = max(1, round(t_pixel * _PI_SAMP_RATE))
        n_collect = n * n_pixels + 1

        self._n_steps_per_pixel = n
        self._n_pixels          = n_pixels

        self.log.debug(
            f'arm  n_pixels={n_pixels}  '
            f't_pixel={t_pixel * 1e3:.3f} ms  '
            f'steps/pixel={n}  '
            f'n_collect={n_collect}'
        )

        try:
            self._create_tasks(n_collect)

        except ni.DaqError as exc:
            self._cleanup_task()

            if exc.error_code == -50103:
                # Resource reserved by another module
                if self._reset_on_conflict:
                    self.log.warning(
                        f'Counter resources reserved by another NI task. '
                        f'reset_device_on_conflict=True -- resetting '
                        f'"{self._device_name}". '
                        f'This stops ALL tasks on the device.'
                    )
                    try:
                        ni.system.Device(self._device_name).reset_device()
                        self.log.info(
                            f'NI device "{self._device_name}" reset successfully.'
                        )
                    except Exception as reset_exc:
                        raise RuntimeError(
                            f'Failed to reset "{self._device_name}": {reset_exc}'
                        ) from reset_exc

                    # Retry once after device reset
                    try:
                        self._create_tasks(n_collect)
                    except ni.DaqError as retry_exc:
                        self._cleanup_task()
                        raise RuntimeError(
                            f'NIAPDScannerCounter.arm failed after device reset: '
                            f'{retry_exc}'
                        ) from retry_exc

                else:
                    raise RuntimeError(
                        f'NIAPDScannerCounter.arm failed: counter resources '
                        f'("{self._counter_channel}" or "{self._clock_counter}") '
                        f'on "{self._device_name}" are reserved by another NI task.\n'
                        f'\n'
                        f'Most likely cause: another Qudi module (e.g. a slow counter, '
                        f'NIXSeriesInStreamer, or time-series counter) is currently '
                        f'running and holding these counters.\n'
                        f'\n'
                        f'Options:\n'
                        f'  1. Deactivate the conflicting Qudi module before scanning.\n'
                        f'  2. Set  reset_device_on_conflict: true  in the YAML config '
                        f'to automatically stop all NI tasks and retry '
                        f'(WARNING: interrupts ALL tasks on "{self._device_name}").'
                    ) from exc

            else:
                raise RuntimeError(
                    f'NIAPDScannerCounter.arm failed: {exc}'
                ) from exc

    def read(self, n_pixels: int) -> Optional[Dict[str, np.ndarray]]:
        """
        Wait for CO to finish, read buffer, return per-pixel counts.

        raw[k]        = cumulative photon count at end of CO clock tick k
        np.diff(raw)  = per-step increments (background subtracted by diff)
        reshape + sum = per-pixel counts

        @param n_pixels : must match value passed to arm()
        @return         : {channel_name: np.ndarray(n_pixels,)} or None
        """
        if self._task is None or self._co_task is None or self._reader is None:
            self.log.error('read() called without active tasks.')
            return None

        n         = self._n_steps_per_pixel
        n_collect = n * n_pixels + 1

        try:
            # Block until CO has generated all n_collect clock pulses
            self._co_task.wait_until_done(timeout=self._read_timeout)
            # CI is clocked by CO and finishes at the same time
            self._task.wait_until_done(timeout=10.0)

            raw = np.zeros(n_collect, dtype=np.float64)
            self._reader.read_many_sample_double(
                raw,
                number_of_samples_per_channel=n_collect,
                timeout=10.0,
            )

        except ni.DaqError as exc:
            self.log.error(
                f'NIAPDScannerCounter.read failed: {exc}\n'
                f'  Confirm BNC: PI Trigger OUT -> NI {self._trigger_terminal}\n'
                f'  Gate must go HIGH for the full scan region duration.'
            )
            return None
        finally:
            self._cleanup_task()

        # np.diff removes background baseline (raw[0] = counts before gate HIGH)
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
    # Task creation
    # =========================================================================

    def _create_tasks(self, n_collect: int) -> None:
        """
        Create and configure CO + CI tasks.

        Separated from arm() so it can be called twice (first attempt + retry
        after device reset) without duplicating the task setup code.

        Raises ni.DaqError directly -- the caller (arm) handles it.

        @param n_collect : total samples to collect (n_pixels * n_steps + 1)
        """
        clock_num = ''.join(filter(str.isdigit, self._clock_counter))
        co_output = f'/{self._device_name}/Ctr{clock_num}InternalOutput'

        # ---- CO task: finite 5 kHz pulse train, triggered by gate ----------
        self._co_task = ni.Task('ScanClock')
        self._co_task.co_channels.add_co_pulse_chan_freq(
            counter       = f'/{self._device_name}/{self._clock_counter}',
            freq          = _PI_SAMP_RATE,
            duty_cycle    = 0.5,
            idle_state    = Level.LOW,
            initial_delay = 0.0,
        )
        self._co_task.timing.cfg_implicit_timing(
            sample_mode    = AcquisitionType.FINITE,
            samps_per_chan = n_collect,
        )
        # CO tasks support start triggers on all NI X-Series devices
        self._co_task.triggers.start_trigger.cfg_dig_edge_start_trig(
            trigger_source = f'/{self._device_name}/{self._trigger_terminal}',
            trigger_edge   = Edge.RISING,
        )

        # ---- CI task: count photons, clocked by CO output ------------------
        self._task = ni.Task('APDScanCounter')
        self._task.ci_channels.add_ci_count_edges_chan(
            f'/{self._device_name}/{self._counter_channel}',
            edge=Edge.RISING,
        )
        # Route APD/SPCM signal to the counter source input
        self._task.ci_channels.all.ci_count_edges_term = (
            f'/{self._device_name}/{self._apd_terminal}'
        )
        # Internal routing: CI clocked by CO output -- always works
        self._task.timing.cfg_samp_clk_timing(
            rate           = _PI_SAMP_RATE,
            source         = co_output,
            active_edge    = Edge.RISING,
            sample_mode    = AcquisitionType.FINITE,
            samps_per_chan = n_collect,
        )

        self._reader = CounterReader(self._task.in_stream)
        self._reader.verify_array_shape = False

        # Register tasks so future arm() calls can stop them if needed
        self._register_tasks()

        # CI starts first -- waits for CO to provide first clock edge
        self._task.start()
        # CO starts -- waits for gate RISING edge on trigger_terminal
        self._co_task.start()

        self.log.debug(
            f'Tasks created and started.  '
            f'CO ({self._clock_counter}) -> CI ({self._counter_channel}) '
            f'via {co_output}'
        )

    # =========================================================================
    # Registry management
    # =========================================================================

    def _register_tasks(self) -> None:
        """Add CI and CO tasks to module-level registry."""
        device = self._device_name.lower()
        with _REGISTRY_LOCK:
            if self._task is not None:
                _TASK_REGISTRY[(device, self._counter_channel.lower())] = self._task
            if self._co_task is not None:
                _TASK_REGISTRY[(device, self._clock_counter.lower())] = self._co_task

    def _unregister_tasks(self) -> None:
        """Remove CI and CO tasks from module-level registry."""
        device = self._device_name.lower()
        with _REGISTRY_LOCK:
            _TASK_REGISTRY.pop((device, self._counter_channel.lower()), None)
            _TASK_REGISTRY.pop((device, self._clock_counter.lower()),   None)

    def _stop_registered_tasks(self) -> None:
        """
        Stop and remove any tasks in the registry using our counter channels.

        This handles zombie tasks left by a previous instance of this class
        that was not properly deactivated (e.g. after a crash).
        Does NOT touch tasks from other modules.
        """
        device = self._device_name.lower()
        channels_needed = [
            (device, self._counter_channel.lower()),
            (device, self._clock_counter.lower()),
        ]

        with _REGISTRY_LOCK:
            for key in channels_needed:
                task = _TASK_REGISTRY.pop(key, None)
                if task is not None:
                    try:
                        if not task.is_task_done():
                            task.stop()
                        task.close()
                        self.log.info(
                            f'Stopped zombie task using '
                            f'"{key[1]}" on "{key[0]}".'
                        )
                    except Exception as exc:
                        self.log.debug(
                            f'Could not stop zombie task {key}: {exc}'
                        )

    # =========================================================================
    # Cleanup
    # =========================================================================

    def _cleanup_task(self) -> None:
        """Stop and close both NI tasks. Safe to call at any time."""
        self._unregister_tasks()
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