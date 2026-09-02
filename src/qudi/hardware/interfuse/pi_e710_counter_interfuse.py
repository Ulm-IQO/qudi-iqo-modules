# -*- coding: utf-8 -*-
"""
PI E-710 + NI-DAQ Counter -- Qudi Scanning Probe Interfuse
==========================================================

Combines:
    scanner:        PIE710Scanner        (pi_e710_scanning_probe.py)
    photon_counter: NIAPDScannerCounter  (ni_apd_scanner_counter.py)

Together they satisfy the complete Qudi ScanningProbeInterface.

Scan execution
--------------
1D scan:
    arm(n_pixels)              <- counter creates CO+CI tasks, both wait
    fire 1D PI scan            <- GPIB command, BLOCKING (includes settle
                                   time + segment/trigger configuration)
    wait_for_scan_complete     <- verify PI generators idle
    read(n_pixels)             <- CO.wait_until_done -> read buffer

2D scan (line by line):
    The PI gate fires once per fast-axis sweep.
    For each slow-axis position:
        move slow axis
        arm(n_fast)                     <- fresh CO+CI tasks
        fire fast-axis sweep (see below) <- BLOCKING
        wait_for_scan_complete
        read(n_fast)
        accumulate + live GUI update

Firing the fast-axis sweep, line by line
------------------------------------------
Since the fast-axis range, pixel dwell time, and trigger mode are identical
across every line of a given 2D scan, only the FIRST line needs the full
scan setup (move, settle, and the ~15-20 GPIB commands that program the
segment waveform and trigger/flag configuration on the PI controller).
Every subsequent line only needs to re-fire that already-configured segment
program, which is done via the scanner module's lightweight
retrigger_line() -- a single short command instead of a full
reconfiguration, substantially reducing dead time between lines. See
PIE710Scanner.retrigger_line() (and the underlying
PIE710Controller.retrigger_line()) for the exact mechanism and a caveat
about the assumption it relies on.

Counter trigger mode
---------------------
The counter_trigger_mode ConfigOption selects which pair of methods on the
connected photon_counter module is called to drive a scan line:

    'clock' (default):
        counter.arm(n_pixels, t_pixel) / counter.read(n_pixels) /
        counter.stop(). Assumes the scan trigger is a fixed-rate clock --
        this is the ORIGINAL behavior of this interfuse, unchanged, and
        remains the default for any existing config that does not set
        counter_trigger_mode at all.

    'position_distance':
        counter.arm_position_trigger(n_pixels, t_pixel) /
        counter.read_position_trigger(n_pixels) /
        counter.stop_position_trigger(). For scanners (e.g. a PI E-727 in
        GCS TriggerMode 0, "Position Distance") whose trigger fires once
        per real physical step of motion rather than at a fixed rate --
        see NIXSeriesCounter's module docstring, "POSITION-DISTANCE
        TRIGGER ACQUISITION MODE", for the acquisition model this uses.

Both method pairs are expected to exist together on the connected
photon_counter module (see ni_x_series_counter.py) -- this option only
selects which pair this interfuse calls; it does not change what the
counter module itself supports.

YAML configuration:
    interfuse:
        confocal_scanner:
            module.Class: 'interfuse.pi_e710_counter_interfuse.PIE710CounterInterfuse'
            connect:
                scanner:        'my_pi_scanner'
                photon_counter: 'my_counter'
            options:
                counter_trigger_mode: 'clock'   # or 'position_distance'
"""

import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.constraints import ScalarConstraint
from qudi.interface.scanning_probe_interface import (
    BackScanCapability,
    ScanConstraints,
    ScanData,
    ScannerAxis,
    ScannerChannel,
    ScanSettings,
    ScanningProbeInterface,
)

# from qudi.hardware.pi_e710_scanning_probe import PIE710ScannerInterface


class PIE710CounterInterfuse(ScanningProbeInterface):
    """
    Qudi ScanningProbeInterface combining PIE710Scanner and NIAPDScannerCounter.

    Motion:   fully delegated to scanner module.
    Counting: fully delegated to counter module.
    Logic:    lives here.

    Axis mapping:
        'x'  ->  PI axis 1   (fast axis in XY, XZ scans)
        'y'  ->  PI axis 2   (fast axis in YZ; slow in XY)
        'z'  ->  PI axis 3   (slow axis in XZ, YZ scans)

    Supported scan combinations:
        1D:  ('x',)  ('y',)  ('z',)
        2D:  ('x','y')  ('x','z')  ('y','z')  -- first = fast axis

    Back scan:
        Accepted so logic validation passes.
        Always returns NaN -- PI gate only active on forward sweep.

    Scan state tracking:
        Uses Qudi module_state ('locked' / 'idle').
        The scanning probe logic polls module_state() to detect completion.
        The scan thread's finally block unlocks module_state when done.

    Counter trigger mode:
        See module docstring, "Counter trigger mode". Controlled by the
        counter_trigger_mode ConfigOption ('clock' default, or
        'position_distance'); dispatched via _counter_arm() /
        _counter_read() / _counter_stop() below.
    """

    _scanner = Connector(name='scanner',        interface='PIE710ScannerInterface')
    _counter = Connector(name='photon_counter', interface='NIXSeriesCounter')

    _counter_trigger_mode = ConfigOption(
        'counter_trigger_mode', default='clock', missing='nothing')

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._constraints:        Optional[ScanConstraints] = None
        self._scan_settings:      Optional[ScanSettings]    = None
        self._scan_data:          Optional[ScanData]        = None
        self._back_scan_settings: Optional[ScanSettings]    = None
        self._back_scan_data:     Optional[ScanData]        = None

        self._scan_thread: Optional[threading.Thread] = None
        self._stop_event   = threading.Event()
        self._lock         = threading.Lock()

    def on_activate(self) -> None:
        mode = self._counter_trigger_mode
        if mode not in ('clock', 'position_distance'):
            raise ValueError(
                f'Invalid counter_trigger_mode "{mode}" -- must be '
                f'"clock" or "position_distance".'
            )
        self._constraints = self._build_constraints()
        self.log.info(
            f'PIE710CounterInterfuse ready. counter_trigger_mode="{mode}"'
        )

    def on_deactivate(self) -> None:
        if self.module_state() == 'locked':
            self.stop_scan()

    # =========================================================================
    # Counter dispatch -- see module docstring, "Counter trigger mode"
    # =========================================================================

    def _counter_arm(self, n_pixels: int, t_pixel: float) -> None:
        counter = self._counter()
        if self._counter_trigger_mode == 'position_distance':
            counter.arm_position_trigger(n_pixels=n_pixels, t_pixel=t_pixel)
        else:
            counter.arm(n_pixels=n_pixels, t_pixel=t_pixel)

    def _counter_read(self, n_pixels: int) -> Optional[Dict[str, np.ndarray]]:
        counter = self._counter()
        if self._counter_trigger_mode == 'position_distance':
            return counter.read_position_trigger(n_pixels=n_pixels)
        return counter.read(n_pixels=n_pixels)

    def _counter_stop(self) -> None:
        counter = self._counter()
        if self._counter_trigger_mode == 'position_distance':
            counter.stop_position_trigger()
        else:
            counter.stop()

    # =========================================================================
    # Constraints
    # =========================================================================

    def _build_constraints(self) -> ScanConstraints:
        """Build ScanConstraints from scanner travel limits and counter channels."""
        scanner = self._scanner()
        counter = self._counter()

        channel_objects = tuple(
            ScannerChannel(
                name=ch,
                unit=counter.channel_units.get(ch, 'c/s'),
                dtype='float64',
            )
            for ch in counter.channel_names
        )

        def _axis_range(name: str) -> Tuple[float, float]:
            """
            Uses scanner.get_scan_safe_range(name) when the connected
            scanner module provides it (e.g. PIE727Scanner, whose
            lin_pts_ratio padding can make a full-travel-range scan
            physically overshoot real limits -- see that module's
            docstring, "SCAN RANGE VS. lin_pts_ratio PADDING"), falling
            back to the plain real travel range otherwise (e.g.
            PIE710Scanner, which has no padding concept and therefore no
            such method) -- duck-typed via getattr() rather than added to
            the shared PIE710ScannerInterface, so this requires no
            changes to that interface or to PIE710Scanner.
            """
            getter = getattr(scanner, 'get_scan_safe_range', None)
            if getter is not None:
                lo, hi = getter(name)
            else:
                lo, hi = {
                    'x': scanner.x_range,
                    'y': scanner.y_range,
                    'z': scanner.z_range,
                }[name]
            return float(lo), float(hi)

        def _make_axis(name: str, lo: float, hi: float) -> ScannerAxis:
            span = float(hi - lo)
            return ScannerAxis(
                name=name,
                unit='um',
                position=ScalarConstraint(
                    default=round((lo + hi) / 2.0, 3),
                    bounds=(float(lo), float(hi)),
                ),
                step=ScalarConstraint(default=0.1, bounds=(1e-3, span)),
                resolution=ScalarConstraint(
                    default=100, bounds=(2, 2000), enforce_int=True),
                frequency=ScalarConstraint(
                    default=1000.0, bounds=(1.0, 5000.0)),
            )

        axis_objects = (
            _make_axis('x', *_axis_range('x')),
            _make_axis('y', *_axis_range('y')),
            _make_axis('z', *_axis_range('z')),
        )

        return ScanConstraints(
            channel_objects=channel_objects,
            axis_objects=axis_objects,
            back_scan_capability=(
                BackScanCapability.AVAILABLE
                | BackScanCapability.RESOLUTION_CONFIGURABLE
            ),
            has_position_feedback=True,
            square_px_only=False,
        )

    @property
    def constraints(self) -> ScanConstraints:
        return self._constraints

    # =========================================================================
    # Scan settings properties
    # =========================================================================

    @property
    def scan_settings(self) -> Optional[ScanSettings]:
        return self._scan_settings

    @property
    def back_scan_settings(self) -> Optional[ScanSettings]:
        return self._back_scan_settings

    # =========================================================================
    # Reset
    # =========================================================================

    def reset(self) -> None:
        if self.module_state() == 'locked':
            self.stop_scan()
        self._scanner().reset()

    # =========================================================================
    # Configure
    # =========================================================================

    def configure_scan(self, settings: ScanSettings) -> None:
        with self._lock:
            if self.module_state() == 'locked':
                self.log.error('Cannot configure scan while scanning.')
                return

            self._constraints.check_settings(settings)

            supported = {
                ('x',), ('y',), ('z',),
                ('x', 'y'), ('x', 'z'), ('y', 'z'),
            }
            if tuple(settings.axes) not in supported:
                raise ValueError(
                    f'Axis combination {settings.axes} not supported. '
                    f'Supported: {supported}'
                )

            self._scan_settings = settings
            self._scan_data = ScanData.from_constraints(
                settings=settings, constraints=self._constraints)

            # Matching default back scan so logic validation always passes
            default_back = ScanSettings(
                channels   = settings.channels,
                axes       = settings.axes,
                range      = settings.range,
                resolution = settings.resolution,
                frequency  = settings.frequency,
            )
            self._back_scan_settings = default_back
            self._back_scan_data = ScanData.from_constraints(
                settings=default_back, constraints=self._constraints)

    def configure_back_scan(self, settings: ScanSettings) -> None:
        """Accept back scan settings. Data will always be NaN."""
        with self._lock:
            if self.module_state() == 'locked':
                self.log.error('Cannot configure back scan while scanning.')
                return
            if self._scan_settings is None:
                self.log.error('Configure forward scan before back scan.')
                return
            self._constraints.check_back_scan_settings(settings, self._scan_settings)
            self._back_scan_settings = settings
            self._back_scan_data = ScanData.from_constraints(
                settings=settings, constraints=self._constraints)

    # =========================================================================
    # Motion -- fully delegated to scanner
    # =========================================================================

    def move_absolute(
        self,
        position: Dict[str, float],
        velocity: Optional[float] = None,
        blocking: bool = False,
    ) -> Dict[str, float]:
        if self.module_state() == 'locked':
            self.log.error('Cannot move while scan is in progress.')
            return self.get_target()
        return self._scanner().move_absolute(position, blocking=blocking)

    def move_relative(
        self,
        distance: Dict[str, float],
        velocity: Optional[float] = None,
        blocking: bool = False,
    ) -> Dict[str, float]:
        if self.module_state() == 'locked':
            self.log.error('Cannot move while scan is in progress.')
            return self.get_target()
        return self._scanner().move_relative(distance, blocking=blocking)

    def get_target(self) -> Dict[str, float]:
        return self._scanner().get_target()

    def get_position(self) -> Dict[str, float]:
        return self._scanner().get_position()

    # =========================================================================
    # Scanning
    # =========================================================================

    def start_scan(self) -> None:
        with self._lock:
            if self.module_state() == 'locked':
                self.log.error('Scan already running.')
                return
            if self._scan_settings is None:
                self.log.error('No scan configured.')
                return

            self._scan_data.new_scan()
            self._scan_data.scanner_target_at_start = self.get_target()

            if self._back_scan_data is not None:
                self._back_scan_data.new_scan()
                self._back_scan_data.scanner_target_at_start = self.get_target()

            self._stop_event.clear()

            # Lock module_state: logic polls this to track scan progress.
            # Scan thread's finally block unlocks it on completion or error.
            self.module_state.lock()

            self._scan_thread = threading.Thread(
                target=self._scan_worker,
                name='PIE710CounterScanWorker',
                daemon=True,
            )
            self._scan_thread.start()

    def stop_scan(self) -> None:
        if self.module_state() != 'locked':
            self.log.debug('stop_scan() called but module_state is not locked.')
            return

        self._stop_event.set()
        self._scanner().halt_generators()
        self._counter_stop()   # aborts CO + CI (+ DI) tasks

        thread = self._scan_thread
        if thread and thread.is_alive():
            thread.join(timeout=10.0)
            if thread.is_alive():
                self.log.warning('Scan thread timeout -- forcing unlock.')
                if self.module_state() == 'locked':
                    self.module_state.unlock()

        self.log.info('Scan stopped.')

    def get_scan_data(self) -> Optional[ScanData]:
        return self._scan_data

    def get_back_scan_data(self) -> Optional[ScanData]:
        """NaN -- PI gate only active on forward sweep."""
        return self._back_scan_data

    def emergency_stop(self) -> None:
        self._stop_event.set()
        self._scanner().halt()
        self._scanner().halt_generators()
        self._counter_stop()
        thread = self._scan_thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)
        if self.module_state() == 'locked':
            self.module_state.unlock()
        self.log.warning('EMERGENCY STOP.')

    # =========================================================================
    # Scan worker
    # =========================================================================

    def _scan_worker(self) -> None:
        """
        Background thread. Dispatches to 1D or 2D handler.
        finally block ALWAYS unlocks module_state so the logic detects completion.
        """
        try:
            s       = self._scan_settings
            t_pixel = 1.0 / s.frequency

            if len(s.axes) == 1:
                self._run_1d_scan(
                    axis       = s.axes[0],
                    scan_range = s.range[0],
                    n_pts      = s.resolution[0],
                    t_pixel    = t_pixel,
                )
            elif len(s.axes) == 2:
                self._run_2d_scan_line_by_line(
                    fast_axis  = s.axes[0],
                    slow_axis  = s.axes[1],
                    fast_range = s.range[0],
                    slow_range = s.range[1],
                    n_fast     = s.resolution[0],
                    n_slow     = s.resolution[1],
                    t_pixel    = t_pixel,
                )
        except Exception as exc:
            self.log.exception(f'Scan worker unhandled exception: {exc}')
        finally:
            if self.module_state() == 'locked':
                self.module_state.unlock()

    # =========================================================================
    # 1D scan
    # =========================================================================

    def _run_1d_scan(
        self,
        axis:       str,
        scan_range: Tuple[float, float],
        n_pts:      int,
        t_pixel:    float,
    ) -> None:
        """
        1. arm(n_pts)             -- CO+CI tasks created; CO waits for gate
        2. start_scan()           -- GPIB: moves to start, configures and
                                      fires the scan (BLOCKING)
        3. wait_for_scan_complete -- verify PI generators idle
        4. read(n_pts)            -- CO.wait_until_done; read buffer; diff+reshape
        5. store
        """
        pos_array   = np.linspace(scan_range[0], scan_range[1], n_pts).tolist()
        current_pos = self._scanner().get_target()

        # 1. Arm counter before scanner fires
        try:
            self._counter_arm(n_pixels=n_pts, t_pixel=t_pixel)
        except Exception as exc:
            self.log.error(f'Counter arm failed: {exc}')
            return

        if self._stop_event.is_set():
            self._counter_stop()
            return

        # 2. Fire PI scan (BLOCKING: includes move, settle, and full
        #    segment/trigger configuration)
        try:
            estimated_s = self._scanner().start_scan(
                axes        = (axis,),
                positions   = (pos_array,),
                t_pixel     = t_pixel,
                current_pos = current_pos,
            )
        except Exception as exc:
            self.log.error(f'Scanner start_scan failed: {exc}')
            self._counter_stop()
            return

        # 3. Verify PI generators idle
        completed = self._scanner().wait_for_scan_complete(
            estimated_s = estimated_s,
            stop_event  = self._stop_event,
        )
        if not completed or self._stop_event.is_set():
            self._counter_stop()
            return

        # 4. Read (CO.wait_until_done returns immediately since scan already done)
        try:
            counts_dict = self._counter_read(n_pixels=n_pts)
        except Exception as exc:
            self.log.error(f'Counter read failed: {exc}')
            counts_dict = None

        # 5. Store
        self._store_1d(counts_dict=counts_dict, n_pts=n_pts, t_pixel=t_pixel)
        self._scanner().sync_position()

    # =========================================================================
    # 2D scan -- line by line
    # =========================================================================

    def _run_2d_scan_line_by_line(
        self,
        fast_axis:  str,
        slow_axis:  str,
        fast_range: Tuple[float, float],
        slow_range: Tuple[float, float],
        n_fast:     int,
        n_slow:     int,
        t_pixel:    float,
    ) -> None:
        """
        2D scan as n_slow individual 1D fast-axis scans.

        The PI gate fires once per fast-axis sweep. The counter re-arms
        before each sweep. After each line, ScanData is updated for live
        GUI display.

        Only the FIRST line calls start_scan(), which performs the full
        move + settle + segment/trigger/flag configuration on the PI
        controller. Every subsequent line calls the lightweight
        retrigger_line() instead, which re-fires the same already-configured
        segment program with a single short command -- safe because the
        fast-axis range, dwell time, and trigger mode are identical across
        all lines of this scan. See PIE710Scanner.retrigger_line() for the
        exact mechanism and its usage requirements.
        """
        fast_pos = np.linspace(fast_range[0], fast_range[1], n_fast).tolist()
        slow_pos = np.linspace(slow_range[0], slow_range[1], n_slow).tolist()
        channels = self._scan_settings.channels

        # Accumulate raw counts; (n_fast, n_slow) -- not yet divided by t_pixel
        data_accum = {
            ch: np.zeros((n_fast, n_slow), dtype=float) for ch in channels
        }

        for i_slow, slow_val in enumerate(slow_pos):
            if self._stop_event.is_set():
                break

            # Move slow axis directly via scanner module
            # (bypasses interfuse.move_absolute which is blocked while locked)
            current_pos = self._scanner().get_target()
            current_pos[slow_axis] = slow_val
            self._scanner().move_absolute(current_pos, blocking=True)

            if self._stop_event.is_set():
                break

            # Arm counter for this line
            try:
                self._counter_arm(n_pixels=n_fast, t_pixel=t_pixel)
            except Exception as exc:
                self.log.error(f'Counter arm failed on line {i_slow}: {exc}')
                break

            if self._stop_event.is_set():
                self._counter_stop()
                break

            # Fire this line's fast-axis sweep: full configuration on the
            # first line, a lightweight retrigger on every line after that.
            try:
                if i_slow == 0:
                    current_pos = self._scanner().get_target()
                    estimated_s = self._scanner().start_scan(
                        axes        = (fast_axis,),
                        positions   = (fast_pos,),
                        t_pixel     = t_pixel,
                        current_pos = current_pos,
                    )
                else:
                    estimated_s = self._scanner().retrigger_line()
            except Exception as exc:
                action = 'start_scan' if i_slow == 0 else 'retrigger_line'
                self.log.error(f'Scanner {action} failed on line {i_slow}: {exc}')
                self._counter_stop()
                break

            # Verify PI idle
            completed = self._scanner().wait_for_scan_complete(
                estimated_s = estimated_s,
                stop_event  = self._stop_event,
            )
            if not completed or self._stop_event.is_set():
                self._counter_stop()
                break

            # Read this line
            try:
                line_counts = self._counter_read(n_pixels=n_fast)
            except Exception as exc:
                self.log.error(f'Counter read failed on line {i_slow}: {exc}')
                line_counts = None

            # Accumulate
            if line_counts is not None:
                for ch in channels:
                    raw = line_counts.get(ch)
                    if raw is not None and len(raw) == n_fast:
                        data_accum[ch][:, i_slow] = raw
                    else:
                        self.log.warning(
                            f'Line {i_slow} channel "{ch}": '
                            f'unexpected array size. Filling zeros.'
                        )

            # Live GUI update after each line
            self._write_2d_scan_data(data_accum, channels, t_pixel)

        # Final write
        self._write_2d_scan_data(data_accum, channels, t_pixel)
        self._scanner().sync_position()

    # =========================================================================
    # Data storage
    # =========================================================================

    def _store_1d(
        self,
        counts_dict: Optional[Dict[str, np.ndarray]],
        n_pts:       int,
        t_pixel:     float,
    ) -> None:
        if self._scan_data is None or counts_dict is None:
            self.log.warning('No 1D count data to store.')
            return

        channels = self._scan_settings.channels
        data_dict: Dict[str, np.ndarray] = {}
        for ch in channels:
            raw = counts_dict.get(ch)
            if raw is not None and len(raw) == n_pts:
                data_dict[ch] = np.asarray(raw, dtype=float) / t_pixel
            else:
                if raw is not None:
                    self.log.warning(
                        f'1D count mismatch "{ch}": '
                        f'got {len(raw)}, expected {n_pts}. Filling zeros.'
                    )
                data_dict[ch] = np.zeros(n_pts, dtype=float)

        try:
            self._scan_data.data = data_dict
        except ValueError as exc:
            self.log.error(f'ScanData 1D write failed: {exc}')

    def _write_2d_scan_data(
        self,
        data_accum: Dict[str, np.ndarray],
        channels:   Tuple[str, ...],
        t_pixel:    float,
    ) -> None:
        """
        Convert accumulated 2D raw counts to c/s and write to ScanData.
        Called after every line for live GUI updates.
        """
        if self._scan_data is None:
            return
        data_dict = {ch: data_accum[ch] / t_pixel for ch in channels}
        try:
            self._scan_data.data = data_dict
        except ValueError as exc:
            self.log.error(f'ScanData 2D write failed: {exc}')