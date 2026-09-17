# -*- coding: utf-8 -*-
"""
PI E-710 + NI-DAQ Counter -- Qudi Scanning Probe Interfuse
==========================================================

Combines:
    scanner:        PIE710Scanner        (pi_e710_scanning_probe.py)
    photon_counter: NIAPDScannerCounter  (ni_apd_scanner_counter.py)

Together they satisfy the complete Qudi ScanningProbeInterface.

Units: all scanner-facing positions/ranges here are SI meters (Qudi's
native unit) -- this interfuse does not assume any particular scale.
Scanner hardware modules are responsible for converting to/from their
own native units (e.g. micrometers) at their own public boundary.

Scan execution
--------------
1D scan:
    arm(n_pixels)              <- counter creates CO+CI tasks, both wait
    fire 1D PI scan             <- GPIB command, BLOCKING (includes settle
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
about the assumption it relies on. This applies to the 'clock' and
'position_distance' counter_trigger_modes only -- see "Counter trigger
mode" below for the third, 'point_by_point' mode, which does not use
continuous ramps or retriggering at all.

Counter trigger mode
---------------------
The counter_trigger_mode ConfigOption selects which trio of methods on the
connected photon_counter module drives scan acquisition:

    'clock' (default):
        counter.arm(n_pixels, t_pixel) / counter.read(n_pixels) /
        counter.stop(). Assumes the scan trigger is a fixed-rate clock --
        this is the ORIGINAL behavior of this interfuse, unchanged, and
        remains the default for any existing config that does not set
        counter_trigger_mode at all. Uses the scanner's continuous-ramp
        start_scan()/retrigger_line() mechanism.

    'position_distance':
        counter.arm_position_trigger(n_pixels, t_pixel) /
        counter.read_position_trigger(n_pixels) /
        counter.stop_position_trigger(). For scanners (e.g. a PI E-727 in
        GCS TriggerMode 0, "Position Distance") whose trigger fires once
        per real physical step of motion rather than at a fixed rate --
        see NIXSeriesCounter's module docstring, "POSITION-DISTANCE
        TRIGGER ACQUISITION MODE" and "EXPECTATION-BASED EDGE MATCHING".
        Also uses the scanner's continuous-ramp mechanism. Real hardware
        testing found this mode has a genuine resolution floor -- for
        scans needing finer resolution than this mode can reliably
        support, use 'point_by_point' below instead.

    'point_by_point':
        counter.arm_point_scan() / counter.count_point(t_pixel) [called
        once per pixel, AFTER moving to that pixel's exact position] /
        counter.disarm_point_scan(). Does NOT use the scanner's
        continuous-ramp mechanism at all -- every pixel (fast AND slow
        axis together, in 2D) is visited by an explicit, individually-
        commanded move_absolute(..., blocking=True) call, which already
        waits for the controller's own genuine on-target confirmation
        before this interfuse counts anything. No resolution floor from
        continuous-motion triggering, at the cost of being slower
        per-pixel than the other two modes. See NIXSeriesCounter's
        module docstring, "POINT-BY-POINT (STEP-AND-SETTLE) ACQUISITION
        MODE".

All three method trios are expected to exist together on the connected
photon_counter module -- this option only selects which trio this
interfuse calls; it does not change what the counter module supports.

Scan range vs. scanner padding requirements
----------------------------------------------
axis.position bounds (built in _build_constraints() below) are always the
scanner's REAL, full travel range -- narrowing them was tried and
reverted, because qudi's scanning_probe_logic and scanning_optimize_logic
both reuse axis.position bounds for ordinary absolute-position
validation, independent of scanning.

Instead, for the 'clock' and 'position_distance' counter_trigger_modes,
if the connected scanner module exposes get_scan_safe_range(axis) (e.g.
PIE727Scanner, whose automatic speed-up/slow-down padding requires
overshoot room beyond [start, stop] that can exceed real travel limits
near the edges of a large scan), configure_scan() below clamps the
REQUESTED scan range into that safe sub-range before actually running
the scan, logging a clear warning explaining what was requested vs. what
will actually be scanned.

For 'point_by_point' mode, this clamping is SKIPPED entirely -- that mode
never uses a continuous ramp or any padding.

This clamping is duck-typed via getattr() -- scanners with no padding
concept (e.g. PIE710Scanner) have no get_scan_safe_range() method, so
clamping is skipped for them too.

Clamping behavior (see _clamp_axis_range()):
    - if the requested window already fits inside the safe range: no
      change.
    - if the requested window's WIDTH fits inside the safe range, but the
      window itself is positioned partly/fully outside it: the window is
      SHIFTED into the safe range, preserving the requested width exactly.
    - if the requested window's WIDTH itself is wider than the safe
      range: it is clipped down to the full safe range.

YAML configuration:
    interfuse:
        confocal_scanner:
            module.Class: 'interfuse.pi_e710_counter_interfuse.PIE710CounterInterfuse'
            connect:
                scanner:        'my_pi_scanner'
                photon_counter: 'my_counter'
            options:
                counter_trigger_mode: 'clock'   # 'clock', 'position_distance', or 'point_by_point'
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

    Units: all axis ranges/positions exchanged with Qudi (constraints,
    move_absolute, scan settings, etc.) are meters -- see module
    docstring. Scanner hardware modules handle any unit conversion of
    their own internally.

    Scan state tracking:
        Uses Qudi module_state ('locked' / 'idle').
        The scanning probe logic polls module_state() to detect completion.
        The scan thread's finally block unlocks module_state when done.

    Counter trigger mode:
        See module docstring, "Counter trigger mode". Controlled by the
        counter_trigger_mode ConfigOption ('clock' default,
        'position_distance', or 'point_by_point'); dispatched in
        _scan_worker() below.

    Scan range clamping:
        See module docstring, "Scan range vs. scanner padding
        requirements". Controlled by whether the connected scanner module
        exposes get_scan_safe_range() AND whether counter_trigger_mode is
        'point_by_point' (never clamped in that mode); dispatched via
        _clamp_scan_settings_to_safe_range() below.
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
        if mode not in ('clock', 'position_distance', 'point_by_point'):
            raise ValueError(
                f'Invalid counter_trigger_mode "{mode}" -- must be '
                f'"clock", "position_distance", or "point_by_point".'
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
        elif self._counter_trigger_mode == 'point_by_point':
            counter.disarm_point_scan()
        else:
            counter.stop()

    # =========================================================================
    # Constraints -- always the scanner's REAL, full travel range (meters).
    # See module docstring, "Scan range vs. scanner padding requirements",
    # for why this is intentional and must not be narrowed here.
    # =========================================================================

    def _build_constraints(self) -> ScanConstraints:
        """Build ScanConstraints from scanner travel limits (meters) and
        counter channels."""
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

        def _make_axis(name: str, lo: float, hi: float) -> ScannerAxis:
            lo, hi = float(lo), float(hi)
            span = hi - lo
            # Step bounds/default scale with the axis' own span, so this
            # works regardless of the scanner's real travel magnitude
            # (meters here) -- fixed absolute literals (e.g. 0.1, 1e-3)
            # only made sense back when ranges were in micrometers.
            step_max     = span if span > 0 else 1.0
            step_min     = step_max * 1e-6
            step_default = step_max / 1000.0
            return ScannerAxis(
                name=name,
                unit='m',
                position=ScalarConstraint(
                    default=(lo + hi) / 2.0,
                    bounds=(lo, hi),
                ),
                step=ScalarConstraint(
                    default=step_default, bounds=(step_min, step_max)),
                resolution=ScalarConstraint(
                    default=100, bounds=(2, 2000), enforce_int=True),
                frequency=ScalarConstraint(
                    default=1000.0, bounds=(1.0, 5000.0)),
            )

        axis_objects = (
            _make_axis('x', *scanner.x_range),
            _make_axis('y', *scanner.y_range),
            _make_axis('z', *scanner.z_range),
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
    # Scan range clamping -- see module docstring, "Scan range vs. scanner
    # padding requirements". All values here are meters; displayed as
    # micrometers in the log message only, for readability.
    # =========================================================================

    def _clamp_scan_settings_to_safe_range(self, settings: ScanSettings) -> ScanSettings:
        """
        Returns settings unchanged if:
          - counter_trigger_mode is 'point_by_point', or
          - the connected scanner has no get_scan_safe_range() method, or
          - the requested range already fits inside the safe range for
            every scanned axis.

        Otherwise returns a NEW ScanSettings with the offending axis/axes'
        range replaced by the clamped window (see _clamp_axis_range()),
        after logging a clear warning per affected axis.
        """
        if self._counter_trigger_mode == 'point_by_point':
            return settings

        scanner = self._scanner()
        getter = getattr(scanner, 'get_scan_safe_range', None)
        if getter is None:
            return settings

        new_range: List[Tuple[float, float]] = []
        clamped_any = False
        t_pixel = 1.0 / settings.frequency

        for axis_idx, (axis_name, (req_lo, req_hi)) in enumerate(
                zip(settings.axes, settings.range)):
            n_points = settings.resolution[axis_idx]
            safe_lo, safe_hi = (float(v) for v in getter(axis_name, t_pixel, n_points))
            lo, hi, changed = self._clamp_axis_range(
                float(req_lo), float(req_hi), safe_lo, safe_hi)
            if changed:
                clamped_any = True
                # Displayed in um for readability; all values stay meters
                # internally (new_range below).
                self.log.warning(
                    f'Requested scan range for axis "{axis_name}" '
                    f'[{req_lo*1e6:.4f}, {req_hi*1e6:.4f}] um exceeds the '
                    f'range this scanner can safely trigger across with '
                    f'its current padding configuration for this scan\'s '
                    f'resolution/frequency '
                    f'[{safe_lo*1e6:.4f}, {safe_hi*1e6:.4f}] um. Scanning '
                    f'[{lo*1e6:.4f}, {hi*1e6:.4f}] um instead -- the '
                    f'resulting image reflects this actual, real scanned '
                    f'range, not the originally requested one.'
                )
            new_range.append((lo, hi))

        if not clamped_any:
            return settings

        return ScanSettings(
            channels   = settings.channels,
            axes       = settings.axes,
            range      = tuple(new_range),
            resolution = settings.resolution,
            frequency  = settings.frequency,
        )

    @staticmethod
    def _clamp_axis_range(
        req_lo: float, req_hi: float, safe_lo: float, safe_hi: float,
    ) -> Tuple[float, float, bool]:
        """
        Returns (lo, hi, changed): (lo, hi) is guaranteed to fit inside
        [safe_lo, safe_hi].

        - If [req_lo, req_hi] already fits: returned unchanged, changed=False.
        - If its WIDTH fits but the window itself is positioned partly/
          fully outside [safe_lo, safe_hi]: SHIFTED into bounds, exact
          width preserved.
        - If its WIDTH itself exceeds (safe_hi - safe_lo): clipped down
          to the full safe range.
        """
        span      = req_hi - req_lo
        safe_span = safe_hi - safe_lo

        if req_lo >= safe_lo and req_hi <= safe_hi:
            return req_lo, req_hi, False

        if span > safe_span:
            return safe_lo, safe_hi, True

        lo, hi = req_lo, req_hi
        if lo < safe_lo:
            shift = safe_lo - lo
            lo += shift
            hi += shift
        elif hi > safe_hi:
            shift = hi - safe_hi
            lo -= shift
            hi -= shift
        return lo, hi, True

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

            # Validated against the scanner's REAL, full travel range --
            # see _build_constraints(). A request within true travel
            # limits always passes this, even if it will go on to be
            # clamped below for padding reasons (skipped entirely in
            # 'point_by_point' mode).
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

            # See module docstring, "Scan range vs. scanner padding
            # requirements" -- no-op for 'point_by_point' mode, and for
            # scanners without get_scan_safe_range() (e.g. PIE710Scanner).
            settings = self._clamp_scan_settings_to_safe_range(settings)

            self._scan_settings = settings
            self._scan_data = ScanData.from_constraints(
                settings=settings, constraints=self._constraints)

            # Matching default back scan so logic validation always passes.
            # Built from the (possibly clamped) forward settings, so back
            # scan range always matches what will actually be scanned.
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
        """
        Accept back scan settings. Data will always be NaN.

        The back scan's RANGE is always forced to exactly match the
        forward scan's range (self._scan_settings.range) -- never
        independently re-clamped here, since check_back_scan_settings()
        requires an exact match. Only resolution/frequency are
        genuinely independent and taken from the given settings as-is.
        """
        with self._lock:
            if self.module_state() == 'locked':
                self.log.error('Cannot configure back scan while scanning.')
                return
            if self._scan_settings is None:
                self.log.error('Configure forward scan before back scan.')
                return

            settings = ScanSettings(
                channels   = settings.channels,
                axes       = settings.axes,
                range      = self._scan_settings.range,
                resolution = settings.resolution,
                frequency  = settings.frequency,
            )
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
        self._counter_stop()   # aborts scan-mode counter tasks

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
        Background thread. Dispatches to the appropriate scan method
        based on counter_trigger_mode and number of scanned axes.
        finally block ALWAYS unlocks module_state so the logic detects
        completion.
        """
        try:
            s       = self._scan_settings
            t_pixel = 1.0 / s.frequency

            if self._counter_trigger_mode == 'point_by_point':
                if len(s.axes) == 1:
                    self._run_1d_scan_point_by_point(
                        axis       = s.axes[0],
                        scan_range = s.range[0],
                        n_pts      = s.resolution[0],
                        t_pixel    = t_pixel,
                    )
                elif len(s.axes) == 2:
                    self._run_2d_scan_point_by_point(
                        fast_axis  = s.axes[0],
                        slow_axis  = s.axes[1],
                        fast_range = s.range[0],
                        slow_range = s.range[1],
                        n_fast     = s.resolution[0],
                        n_slow     = s.resolution[1],
                        t_pixel    = t_pixel,
                    )
            else:
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
    # 1D scan -- ramp-based ('clock' / 'position_distance' modes)
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
    # 2D scan -- line by line, ramp-based ('clock' / 'position_distance' modes)
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

        Only the FIRST line calls start_scan() (full move + settle +
        segment/trigger configuration). Every subsequent line calls the
        lightweight retrigger_line() instead, re-firing the same
        already-configured segment program -- safe since fast-axis
        range, dwell time, and trigger mode are identical across all
        lines of this scan.
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
    # 1D scan -- point-by-point (step-and-settle) mode
    # =========================================================================

    def _run_1d_scan_point_by_point(
        self,
        axis:       str,
        scan_range: Tuple[float, float],
        n_pts:      int,
        t_pixel:    float,
    ) -> None:
        """
        For each pixel: move_absolute(..., blocking=True) [which already
        waits for real on-target settling] to that pixel's exact
        position, then count_point(t_pixel) real APD edges. No ramp, no
        triggering, no padding.
        """
        pos_array   = np.linspace(scan_range[0], scan_range[1], n_pts).tolist()
        current_pos = self._scanner().get_target()
        channels    = self._scan_settings.channels

        try:
            self._counter().arm_point_scan()
        except Exception as exc:
            self.log.error(f'Counter arm_point_scan failed: {exc}')
            return

        counts = np.zeros(n_pts, dtype=float)
        try:
            for i, val in enumerate(pos_array):
                if self._stop_event.is_set():
                    break
                target = dict(current_pos)
                target[axis] = val
                self._scanner().move_absolute(target, blocking=True)
                try:
                    counts[i] = self._counter().count_point(t_pixel)
                except Exception as exc:
                    self.log.error(f'count_point failed at pixel {i}: {exc}')
                    counts[i] = 0.0
        finally:
            self._counter().disarm_point_scan()

        counts_dict = {ch: counts for ch in channels}
        self._store_1d(counts_dict=counts_dict, n_pts=n_pts, t_pixel=t_pixel)
        self._scanner().sync_position()

    # =========================================================================
    # 2D scan -- point-by-point (step-and-settle) mode
    # =========================================================================

    def _run_2d_scan_point_by_point(
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
        For each pixel (both axes together): move_absolute(..., blocking=True)
        to that exact (fast, slow) position, then count_point(t_pixel)
        real APD edges. No ramp, no triggering, no padding, no
        retrigger_line() -- structurally independent of the
        continuous-ramp mechanism used by the other two modes.
        """
        fast_pos = np.linspace(fast_range[0], fast_range[1], n_fast).tolist()
        slow_pos = np.linspace(slow_range[0], slow_range[1], n_slow).tolist()
        channels = self._scan_settings.channels

        data_accum = {
            ch: np.zeros((n_fast, n_slow), dtype=float) for ch in channels
        }

        try:
            self._counter().arm_point_scan()
        except Exception as exc:
            self.log.error(f'Counter arm_point_scan failed: {exc}')
            return

        try:
            for i_slow, slow_val in enumerate(slow_pos):
                if self._stop_event.is_set():
                    break
                for i_fast, fast_val in enumerate(fast_pos):
                    if self._stop_event.is_set():
                        break
                    target = {fast_axis: fast_val, slow_axis: slow_val}
                    self._scanner().move_absolute(target, blocking=True)
                    try:
                        count = self._counter().count_point(t_pixel)
                    except Exception as exc:
                        self.log.error(
                            f'count_point failed at '
                            f'(fast={i_fast}, slow={i_slow}): {exc}')
                        count = 0.0
                    for ch in channels:
                        data_accum[ch][i_fast, i_slow] = count
                self._write_2d_scan_data(data_accum, channels, t_pixel)
        finally:
            self._counter().disarm_point_scan()

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