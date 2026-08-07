# -*- coding: utf-8 -*-
"""
PI E-710 + NI-DAQ Counter — Qudi Scanning Probe Interfuse
==========================================================

Combines:
    scanner:        PIE710Scanner        (pi_e710_scanning_probe.py)
    photon_counter: NIAPDScannerCounter  (ni_apd_scanner_counter.py)

Together they satisfy the complete Qudi ScanningProbeInterface.

The scanning probe logic monitors self._scanner().module_state() to track
scan progress.  This interfuse locks module_state when a scan starts and
unlocks it when the scan thread exits — that is the only mechanism needed.

Example config for copy-paste:

    interfuse:
        confocal_scanner:
            module.Class: 'interfuse.pi_e710_counter_interfuse.PIE710CounterInterfuse'
            connect:
                scanner:        'my_pi_scanner'
                photon_counter: 'my_counter'
"""

import threading
from typing import Dict, Optional, Tuple

import numpy as np

from qudi.core.connector import Connector
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
    Qudi ScanningProbeInterface combining a PI E-710 scanner and an NI-DAQ
    photon counter.

    Scan state is tracked exclusively via Qudi's module_state:
        'idle'   — no scan running, motion is allowed
        'locked' — scan running, motion is blocked

    The logic polls module_state() on this interfuse.  When it transitions
    from 'locked' → 'idle' the logic knows the scan is done and calls
    stop_scan() / emits signals itself.

    Back scan:
        Accepted and stored so the logic validation passes.
        Always returns NaN-filled data — the PI E-710 only gates photon
        triggers on the forward sweep.
    """

    _scanner = Connector(name='scanner',        interface='PIE710ScannerInterface')
    _counter = Connector(name='photon_counter', interface='NIAPDScannerCounter')

    # ══════════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._constraints:        Optional[ScanConstraints] = None
        self._scan_settings:      Optional[ScanSettings]    = None
        self._scan_data:          Optional[ScanData]        = None
        self._back_scan_settings: Optional[ScanSettings]    = None
        self._back_scan_data:     Optional[ScanData]        = None

        self._scan_thread: Optional[threading.Thread] = None
        self._stop_event   = threading.Event()
        # Protects configure_scan / start_scan from concurrent calls
        self._lock         = threading.Lock()

    def on_activate(self) -> None:
        self._constraints = self._build_constraints()
        self.log.info('PIE710CounterInterfuse ready.')

    def on_deactivate(self) -> None:
        if self.module_state() == 'locked':
            self.stop_scan()

    # ══════════════════════════════════════════════════════════════════════════
    # Constraints
    # ══════════════════════════════════════════════════════════════════════════

    def _build_constraints(self) -> ScanConstraints:
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
            span = float(hi - lo)
            return ScannerAxis(
                name=name,
                unit='µm',
                position=ScalarConstraint(
                    default=round((lo + hi) / 2.0, 3),
                    bounds=(float(lo), float(hi)),
                ),
                step=ScalarConstraint(
                    default=0.1,
                    bounds=(1e-3, span),
                ),
                resolution=ScalarConstraint(
                    default=100,
                    bounds=(2, 2000),
                    enforce_int=True,
                ),
                frequency=ScalarConstraint(
                    default=1000.0,
                    bounds=(1.0, 5000.0),
                ),
            )

        axis_objects = (
            _make_axis('x', *scanner.x_range),
            _make_axis('y', *scanner.y_range),
            _make_axis('z', *scanner.z_range),
        )

        return ScanConstraints(
            channel_objects=channel_objects,
            axis_objects=axis_objects,
            # AVAILABLE so the logic's on_activate validation passes.
            # RESOLUTION_CONFIGURABLE so the logic can set a default back-scan
            # resolution independently of the forward scan.
            # We accept the settings but always return NaN data for the back
            # sweep because the PI only gates triggers on the forward sweep.
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

    # ══════════════════════════════════════════════════════════════════════════
    # Scan settings  (read-only properties)
    # ══════════════════════════════════════════════════════════════════════════

    @property
    def scan_settings(self) -> Optional[ScanSettings]:
        return self._scan_settings

    @property
    def back_scan_settings(self) -> Optional[ScanSettings]:
        return self._back_scan_settings

    # ══════════════════════════════════════════════════════════════════════════
    # Reset
    # ══════════════════════════════════════════════════════════════════════════

    def reset(self) -> None:
        if self.module_state() == 'locked':
            self.stop_scan()
        self._scanner().reset()

    # ══════════════════════════════════════════════════════════════════════════
    # Configure
    # ══════════════════════════════════════════════════════════════════════════

    def configure_scan(self, settings: ScanSettings) -> None:
        with self._lock:
            if self.module_state() == 'locked':
                self.log.error('Cannot configure scan while scanning is in progress.')
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

            # Always create a matching default back scan so the logic has
            # valid settings from the moment the forward scan is configured.
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
        Accept and store back scan settings so the logic validation passes.
        The returned data will always be NaN-filled — no photons are collected
        on the PI's return sweep.
        """
        with self._lock:
            if self.module_state() == 'locked':
                self.log.error('Cannot configure back scan while scanning is in progress.')
                return
            if self._scan_settings is None:
                self.log.error('Configure forward scan before back scan.')
                return

            self._constraints.check_back_scan_settings(settings, self._scan_settings)
            self._back_scan_settings = settings
            self._back_scan_data = ScanData.from_constraints(
                settings=settings, constraints=self._constraints)

    # ══════════════════════════════════════════════════════════════════════════
    # Motion  —  fully delegated to scanner
    # ══════════════════════════════════════════════════════════════════════════

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

    # ══════════════════════════════════════════════════════════════════════════
    # Scanning
    # ══════════════════════════════════════════════════════════════════════════

    def start_scan(self) -> None:
        with self._lock:
            if self.module_state() == 'locked':
                self.log.error('Scan already running.')
                return
            if self._scan_settings is None:
                self.log.error('No scan configured — call configure_scan() first.')
                return

            self._scan_data.new_scan()
            self._scan_data.scanner_target_at_start = self.get_target()

            if self._back_scan_data is not None:
                self._back_scan_data.new_scan()
                self._back_scan_data.scanner_target_at_start = self.get_target()

            self._stop_event.clear()

            # Lock module_state HERE.
            # The scanning probe logic polls self._scanner().module_state().
            # While 'locked' → logic keeps polling and emitting live updates.
            # When 'idle'    → logic calls its own stop_scan() and emits signals.
            self.module_state.lock()

            self._scan_thread = threading.Thread(
                target=self._scan_worker,
                name='PIE710CounterScanWorker',
                daemon=True,
            )
            self._scan_thread.start()

    def stop_scan(self) -> None:
        """
        Stop a running scan.

        Can be called either:
          (a) by the user / GUI while the scan is still in progress, or
          (b) by the logic's poll loop after our module_state became 'idle'
              (scan completed naturally) — in that case module_state is
              already 'idle' and we return immediately.
        """
        if self.module_state() != 'locked':
            self.log.debug('stop_scan() called but module_state is not locked — nothing to do.')
            return

        # Signal the scan thread to abort
        self._stop_event.set()
        self._scanner().halt_generators()
        self._counter().stop()

        # Join the thread.  The finally block in _scan_worker will unlock
        # module_state, so by the time join() returns the state is clean.
        thread = self._scan_thread
        if thread and thread.is_alive():
            thread.join(timeout=10.0)
            if thread.is_alive():
                self.log.warning('Scan thread did not stop within timeout — forcing unlock.')
                if self.module_state() == 'locked':
                    self.module_state.unlock()

        self.log.info('Scan stopped.')

    def get_scan_data(self) -> Optional[ScanData]:
        return self._scan_data

    def get_back_scan_data(self) -> Optional[ScanData]:
        """Returns NaN-filled ScanData — PI triggers only on forward sweep."""
        return self._back_scan_data

    def emergency_stop(self) -> None:
        self._stop_event.set()
        self._scanner().halt()
        self._scanner().halt_generators()
        self._counter().stop()

        thread = self._scan_thread
        if thread and thread.is_alive():
            thread.join(timeout=5.0)

        if self.module_state() == 'locked':
            self.module_state.unlock()

        self.log.warning('EMERGENCY STOP.')

    # ══════════════════════════════════════════════════════════════════════════
    # Scan worker
    # ══════════════════════════════════════════════════════════════════════════

    def _scan_worker(self) -> None:
        """
        Background thread.  Sequence:
            1. Arm counter              (before PI fires)
            2. Start scanner waveform   (PI runs autonomously over GPIB)
            3. Wait for PI to finish
            4. Read counts from counter
            5. Store data into ScanData

        The finally block ALWAYS unlocks module_state, regardless of how the
        thread exits.  This is how the logic detects scan completion.
        """
        try:
            s       = self._scan_settings
            t_pixel = 1.0 / s.frequency

            positions = tuple(
                np.linspace(r[0], r[1], n).tolist()
                for r, n in zip(s.range, s.resolution)
            )
            n_total     = int(np.prod(s.resolution))
            current_pos = self._scanner().get_target()

            # ── 1. Arm counter BEFORE scanner fires ───────────────────────
            try:
                self._counter().arm(n_pixels=n_total, t_pixel=t_pixel)
            except Exception as exc:
                self.log.error(f'Counter arm failed: {exc}')
                return

            if self._stop_event.is_set():
                self._counter().stop()
                return

            # ── 2. Start scanner waveform (non-blocking over GPIB) ────────
            try:
                estimated_s = self._scanner().start_scan(
                    axes        = s.axes,
                    positions   = positions,
                    t_pixel     = t_pixel,
                    current_pos = current_pos,
                )
            except Exception as exc:
                self.log.error(f'Scanner start_scan failed: {exc}')
                self._counter().stop()
                return

            # ── 3. Wait for PI generators to go idle ──────────────────────
            completed = self._scanner().wait_for_scan_complete(
                estimated_s = estimated_s,
                stop_event  = self._stop_event,
            )

            if not completed or self._stop_event.is_set():
                self._counter().stop()
                return

            # ── 4. Read photon counts ─────────────────────────────────────
            try:
                counts_dict = self._counter().read(n_pixels=n_total)
            except Exception as exc:
                self.log.error(f'Counter read failed: {exc}')
                counts_dict = None

            # ── 5. Store into ScanData ────────────────────────────────────
            self._store_data(counts_dict=counts_dict, settings=s, t_pixel=t_pixel)

            self._scanner().sync_position()

        except Exception as exc:
            self.log.exception(f'Scan worker unhandled exception: {exc}')

        finally:
            # Unlock module_state so the logic detects scan completion.
            # This runs whether the scan succeeded, failed, or was aborted.
            if self.module_state() == 'locked':
                self.module_state.unlock()

    # ══════════════════════════════════════════════════════════════════════════
    # Data storage
    # ══════════════════════════════════════════════════════════════════════════

    def _store_data(
        self,
        counts_dict: Optional[Dict[str, np.ndarray]],
        settings:    ScanSettings,
        t_pixel:     float,
    ) -> None:
        """
        Convert raw counts → counts/s and write into self._scan_data.

        1D : data shape  (n_x,)
        2D : counter delivers samples row-major (line by line):
                 index k = i_slow * n_fast + i_fast
             reshape (n_slow, n_fast) → .T → (n_fast, n_slow)
             matches Qudi ScanData convention: data[i_fast, i_slow]
        """
        if self._scan_data is None or counts_dict is None:
            self.log.warning('No count data to store — scan data remains NaN.')
            return

        channels   = settings.channels
        resolution = settings.resolution

        if settings.scan_dimension == 1:
            n_pts = resolution[0]

            def _to_rate(raw: Optional[np.ndarray]) -> np.ndarray:
                if raw is not None and len(raw) == n_pts:
                    return np.asarray(raw, dtype=float) / t_pixel
                if raw is not None:
                    self.log.warning(
                        f'1D count array length mismatch '
                        f'(got {len(raw)}, expected {n_pts}). Filling zeros.'
                    )
                return np.zeros(n_pts, dtype=float)

        else:
            n_fast, n_slow = resolution[0], resolution[1]
            n_total = n_fast * n_slow

            def _to_rate(raw: Optional[np.ndarray]) -> np.ndarray:
                if raw is not None and len(raw) == n_total:
                    return (
                        np.asarray(raw, dtype=float)
                        .reshape(n_slow, n_fast)
                        .T
                        / t_pixel
                    )
                if raw is not None:
                    self.log.warning(
                        f'2D count array length mismatch '
                        f'(got {len(raw)}, expected {n_total}). Filling zeros.'
                    )
                return np.zeros((n_fast, n_slow), dtype=float)

        data_dict = {ch: _to_rate(counts_dict.get(ch)) for ch in channels}

        try:
            self._scan_data.data = data_dict
        except ValueError as exc:
            self.log.error(f'ScanData write failed: {exc}')