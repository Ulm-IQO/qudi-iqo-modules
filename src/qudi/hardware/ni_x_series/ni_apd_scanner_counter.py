# -*- coding: utf-8 -*-
"""
NI-DAQ APD Scanner Counter
===========================

Standalone Qudi hardware module that counts photon pulses from an APD or SPCM,
gated by an external TTL trigger from the PI E-710 scanner.

How the PI E-710 trigger works
-------------------------------
KT-1 (SPCM mode) outputs one active-LOW short pulse per waveform step (0.2 ms
period at 5000 Hz).  On a scope at 10 ms/div the pulses merge into a solid LOW
level -- zoom to < 0.2 ms/div to resolve individual pulses.

The NI counter latches its cumulative photon count on each RISING edge of the
trigger signal (= end of each LOW pulse = return to HIGH).

n = round(t_pixel * 5000) trigger pulses per pixel
n * n_pixels total pulses per scan

NI task design  (matches MATLAB PiezoSetEdgeCountMeas)
-------------------------------------------------------
  ci_count_edges_chan:  APD terminal -> photon edge source (cumulative)
  cfg_samp_clk_timing:  trigger terminal -> sample clock
      rate hint = 1e7 Hz  (MATLAB max_freq = 1e7; must be >= actual rate)
      mode      = CONTINUOUS  (not FINITE -- avoids wait_until_done timeout)
      buffer    = n * n_pixels + headroom

  After scan:  read n*n_pixels + 1 cumulative values
               np.diff -> per-step increments
               reshape(n_pixels, n).sum(axis=1) -> per-pixel counts

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
                apd_terminal:     'PFI8'
                trigger_terminal: 'PFI1'
                channel_name:     'APD1'
                read_timeout:     10.0
"""

import time
import numpy as np
import nidaqmx as ni
from nidaqmx.constants import Edge, AcquisitionType
from nidaqmx.stream_readers import CounterReader
from typing import Dict, List, Optional

from qudi.core.configoption import ConfigOption
from qudi.core.module import Base


_PI_SAMP_RATE: float = 5000.0   # PI waveform generator rate (Hz)
_NI_RATE_HINT: float = 1e7      # Rate hint to NI driver -- must be >= actual rate
                                 # Matches MATLAB: max_freq = 1e7


class NIAPDScannerCounter(Base):
    """
    NI-DAQ photon counter for triggered scanning.

    Three methods consumed by PIE710CounterInterfuse:
        arm(n_pixels, t_pixel)   -- configure and start NI task
        read(n_pixels)           -- read and return per-pixel counts
        stop()                   -- abort immediately, never raises

    Diagnostic method:
        diagnose_trigger_input() -- verify NI receives the PI trigger signal
                                    call this BEFORE running a scan to confirm wiring
    """

    _device_name      = ConfigOption('device_name',      missing='error')
    _counter_channel  = ConfigOption('counter_channel',  default='ctr0')
    _apd_terminal     = ConfigOption('apd_terminal',     missing='error')
    _trigger_terminal = ConfigOption('trigger_terminal', missing='error')
    _channel_name     = ConfigOption('channel_name',     default='APD1')
    _read_timeout     = ConfigOption('read_timeout',     default=10.0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._task:              Optional[ni.Task]       = None
        self._reader:            Optional[CounterReader] = None
        self._n_steps_per_pixel: int                     = 1
        self._n_pixels:          int                     = 0

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def on_activate(self) -> None:
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

        self.log.info(
            f'NIAPDScannerCounter ready -- '
            f'device={self._device_name}  '
            f'counter={self._counter_channel}  '
            f'APD terminal={self._apd_terminal}  '
            f'trigger terminal={self._trigger_terminal}  '
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
        return {self._channel_name: 'c/s'}

    # =========================================================================
    # Diagnostic
    # =========================================================================

    def diagnose_trigger_input(self, test_duration: float = 5.0) -> int:
        """
        Count rising edges directly on trigger_terminal for test_duration seconds.

        Call this WITHOUT a scan running to verify physical wiring.
        Then run a PI scan manually via GPIB or the original test script
        and call again to confirm pulses arrive during scanning.

        How to use
        ----------
        1. Call diagnose_trigger_input(5.0)
        2. While it is counting, run a manual PI scan (or wait if a scan
           happens automatically)
        3. Check the returned count:
             0       -> no signal on trigger_terminal -> CHECK WIRING
             1       -> gate signal (one long pulse) -> scope issue or PI
                        sending level not pulses
             ~n*px   -> correct individual pulses per step

        This uses a DIRECT edge count on the trigger terminal (no APD
        involved) so it isolates the PI->NI connection completely.

        @param test_duration : seconds to listen for edges
        @return              : number of rising edges detected
        """
        ctr_name  = f'/{self._device_name}/{self._counter_channel}'
        trig_name = f'/{self._device_name}/{self._trigger_terminal}'

        count = 0
        try:
            with ni.Task() as task:
                # Count rising edges on the trigger terminal directly
                task.ci_channels.add_ci_count_edges_chan(
                    ctr_name,
                    edge=Edge.RISING,
                )
                # Route trigger terminal as the counter source (not the APD)
                task.ci_channels.all.ci_count_edges_term = trig_name

                # Simple on-demand mode: start, wait, read accumulated count
                task.start()
                self.log.info(
                    f'Listening for rising edges on {self._trigger_terminal} '
                    f'for {test_duration:.1f} s ...'
                )
                time.sleep(test_duration)
                count = int(task.read())
                task.stop()

        except ni.DaqError as exc:
            self.log.error(f'diagnose_trigger_input failed: {exc}')
            return -1

        self.log.info(
            f'diagnose_trigger_input result: {count} rising edges detected '
            f'on {self._trigger_terminal} in {test_duration:.1f} s'
        )

        if count == 0:
            self.log.warning(
                f'ZERO edges on {self._trigger_terminal}.\n'
                f'Checklist:\n'
                f'  1. BNC cable: PI E-710 Trigger OUT -> NI {self._trigger_terminal}\n'
                f'  2. Config option trigger_terminal matches the physical connector\n'
                f'  3. PI scan was running during the test window\n'
                f'  4. PI firmware: FT trigger commands active (check scan_x code)\n'
                f'  5. Scope: set timebase < 0.2 ms/div to see individual KT-1 pulses'
            )
        elif count == 1:
            self.log.warning(
                f'Only 1 edge detected -- PI may be sending a GATE (one long pulse) '
                f'instead of individual pulses per step.\n'
                f'Check PI firmware trigger mode: KT-1 should give one short pulse '
                f'per waveform step, not a single gate.'
            )
        else:
            self.log.info(
                f'Trigger input OK: {count} pulses received.\n'
                f'Expected ~{int(count)} pulses for the scan that ran during the test.'
            )

        return count

    # =========================================================================
    # Counting API
    # =========================================================================

    def arm(self, n_pixels: int, t_pixel: float) -> None:
        """
        Configure and start the NI counter task.

        Call BEFORE sending the PI scan command.

        NI task setup  (matches MATLAB PiezoSetEdgeCountMeas)
        -------------------------------------------------------
        ci_count_edges_chan
            source = apd_terminal  (cumulative photon edge count, never reset)

        cfg_samp_clk_timing
            source      = trigger_terminal  (PI E-710 Trigger OUT)
            active_edge = RISING
                PI KT-1 = active-LOW short pulse.
                Signal is HIGH at rest, dips LOW briefly each waveform step.
                RISING = end of each dip = one latch per step.
            rate        = 1e7 Hz hint (NOT 5000 -- must be >= actual rate)
            mode        = CONTINUOUS  (FINITE causes wait_until_done timeout)
            buffer      = n_steps * n_pixels + 200

        @param n_pixels : total pixels  (1D: n_x,  2D: n_x * n_y)
        @param t_pixel  : dwell time per pixel in seconds
        """
        self._cleanup_task()

        n = max(1, round(t_pixel * _PI_SAMP_RATE))
        n_samples_total = n * n_pixels

        self._n_steps_per_pixel = n
        self._n_pixels          = n_pixels

        self.log.debug(
            f'arm  n_pixels={n_pixels}  '
            f't_pixel={t_pixel * 1e3:.3f} ms  '
            f'steps/pixel={n}  '
            f'expected trigger pulses={n_samples_total}'
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
                rate           = _NI_RATE_HINT,
                source         = f'/{self._device_name}/{self._trigger_terminal}',
                active_edge    = Edge.RISING,
                sample_mode    = AcquisitionType.CONTINUOUS,
                samps_per_chan = n_samples_total + 200,
            )

            self._reader = CounterReader(self._task.in_stream)
            self._reader.verify_array_shape = False

            self._task.start()

        except ni.DaqError as exc:
            self._cleanup_task()
            raise RuntimeError(f'NIAPDScannerCounter.arm failed: {exc}') from exc

    def read(self, n_pixels: int) -> Optional[Dict[str, np.ndarray]]:
        """
        Read and bin per-pixel counts from the continuous NI buffer.

        Matches MATLAB:
            [A,...] = PiezoReadCounter(hCounter, NT+1, timeout)
            A = diff(A)
            B = reshape(A, n, NX)
            C = mean(B, 1)

        Steps
        -----
        1. Short settle delay for last trigger pulses in transit
        2. Check how many samples arrived (diagnose if 0)
        3. Read n*n_pixels + 1 cumulative values
        4. np.diff -> per-step increments
        5. reshape(n_pixels, n).sum(axis=1) -> per-pixel counts

        @param n_pixels : must match value passed to arm()
        @return         : {channel_name: np.ndarray(n_pixels,)} or None
        """
        if self._task is None or self._reader is None:
            self.log.error('read() called but no counter task is active.')
            return None

        n               = self._n_steps_per_pixel
        n_expected      = n * n_pixels
        n_to_read       = n_expected + 1    # +1 so diff gives n_expected values

        # Short settle -- last pulses may still be in transit from PI to NI
        time.sleep(0.3)

        available = self._task.in_stream.avail_samp_per_chan
        self.log.debug(
            f'read  expected={n_expected}  available={available}  '
            f'steps/pixel={n}  n_pixels={n_pixels}'
        )

        if available == 0:
            self.log.warning(
                f'ZERO trigger pulses received on {self._trigger_terminal}.\n'
                f'Run diagnose_trigger_input() to verify wiring:\n'
                f'  PI E-710 Trigger OUT -> NI {self._trigger_terminal}\n'
                f'  Config: trigger_terminal = "{self._trigger_terminal}"\n'
                f'Scope tip: set timebase < 0.2 ms/div to see individual KT-1 pulses.'
            )
            self._cleanup_task()
            return None

        if available < n_expected:
            self.log.warning(
                f'Partial trigger count: {available} received, {n_expected} expected. '
                f'Will use available samples and zero-pad the rest.'
            )

        actual_to_read = min(available, n_to_read)

        try:
            raw = np.zeros(actual_to_read, dtype=np.float64)
            self._reader.read_many_sample_double(
                raw,
                number_of_samples_per_channel=actual_to_read,
                timeout=self._read_timeout,
            )
        except ni.DaqError as exc:
            self.log.error(f'NIAPDScannerCounter.read failed: {exc}')
            self._cleanup_task()
            return None

        # Cumulative -> per-step increments
        increments = np.diff(np.concatenate([[0.0], raw]))

        # Pad or trim to exactly n_expected values
        if len(increments) < n_expected:
            increments = np.concatenate([
                increments,
                np.zeros(n_expected - len(increments))
            ])
        else:
            increments = increments[:n_expected]

        # Sum every n increments -> per-pixel counts
        counts = increments.reshape(n_pixels, n).sum(axis=1)

        self.log.debug(
            f'read OK  total={int(counts.sum())}  '
            f'mean={counts.mean():.1f}  max={counts.max():.0f} cts/px'
        )

        self._cleanup_task()
        return {self._channel_name: counts}

    def stop(self) -> None:
        """Abort immediately. Must never raise exceptions."""
        try:
            self._cleanup_task()
        except Exception as exc:
            self.log.warning(f'NIAPDScannerCounter.stop: {exc}')

    # =========================================================================
    # Internal
    # =========================================================================

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