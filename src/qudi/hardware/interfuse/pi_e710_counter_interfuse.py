# -*- coding: utf-8 -*-
"""
PI E-710 + NI-DAQ Counter — Qudi Scanning Probe Interfuse
==========================================================

Combines:
    scanner:        PIE710Scanner          (pi_e710_scanning_probe.py)
    photon_counter: NIAPDScannerCounter    (ni_apd_scanner_counter.py)

Together they satisfy the complete Qudi ScanningProbeInterface.

Example config for copy-paste:

    interfuse:
        confocal_scanner:
            module.Class: 'interfuse.pi_e710_counter_interfuse.PIE710CounterInterfuse'
            connect:
                scanner:        'my_pi_scanner'
                photon_counter: 'my_counter'
"""

import threading
from typing import Dict, List, Optional, Tuple

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

from qudi.hardware.pi_scanner.pi_e710_scanning_probe import PIE710ScannerInterface


class PIE710CounterInterfuse(ScanningProbeInterface):
    """
    Qudi ScanningProbeInterface combining a PI E-710 scanner and an NI-DAQ
    photon counter.

    Motion is fully delegated to the scanner module.
    Photon counting is fully delegated to the counter module.
    All ScanningProbeInterface logic lives here.

    Axis mapping (defined by PIE710Scanner):
        'x'  →  PI axis 1   (fast axis in XY, XZ scans)
        'y'  →  PI axis 2   (fast axis in YZ; slow axis in XY)
        'z'  →  PI axis 3   (slow axis in XZ, YZ scans)

    Supported scan axis combinations:
        1D:  ('x',)  ('y',)  ('z',)
        2D:  ('x','y')  ('x','z')  ('y','z')  — first element = fast axis

    Back scan:
        The PI E-710 physically performs a return sweep but only gates
        photon triggers on the forward sweep.  Back scan settings are
        accepted so the Qudi logic and GUI function normally, but the
        returned back scan data will always be NaN-filled.
    """

    _scanner = Connector(name='scanner',        interface='PIE710ScannerInterface')
    _counter = Connector(name='photon_counter', interface='NIAPDScannerCounter')

    # ══════════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════════════════════════════════════

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._constraints:        Optional[ScanConstraints]  = None
        self._scan_settings:      Optional[ScanSettings]     = None
        self._scan_data:          Optional[ScanData]         = None
        self._back_scan_settings: Optional[ScanSettings]     = None
        self._back_scan_data:     Optional[ScanData]         = None

        self._is_scanning: bool                       = False
        self._scan_thread: Optional[threading.Thread] = None
        self._stop_event   = threading.Event()
        self._lock         = threading.Lock()

    def on_activate(self) -> None:
        self._constraints = self._build_constraints()
        self.log.info('PIE710CounterInterfuse ready.')

    def on_deactivate(self) -> None:
        if self._is_scanning:
            self.stop_scan()

    # ══════════════════════════════════════════════════════════════════════════
    # Constraints
    # ══════════════════════════════════════════════════════════════════════════

    def _build_constraints(self) -> ScanConstraints:
        """
        Build ScanConstraints from scanner travel limits and counter channel info.
        Called once during on_activate.
        """
        scanner = self._scanner()
        counter = self._counter()

        # Channels: names and units come from the counter module
        channel_objects = tuple(
            ScannerChannel(
                name=ch,
                unit=counter.channel_units.get(ch, 'c/s'),
                dtype='float64',
            )
            for ch in counter.channel_names
        )

        # Axes: travel limits come from the scanner module
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
            # AVAILABLE + RESOLUTION_CONFIGURABLE:
            #   - AVAILABLE so the logic validation in on_activate passes
            #   - RESOLUTION_CONFIGURABLE so the logic can set a default back
            #     scan resolution independently of the forward scan
            # We accept and store back scan settings but always return NaN data
            # because the PI only gates triggers on the forward sweep.
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
    # Scan settings properties
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
        with self._lock:
            if self._is_scanning:
                self._stop_event.set()
                self._scanner().halt_generators()
                self._counter().stop()
                self._is_scanning = False
            self._scanner().reset()

    # ══════════════════════════════════════════════════════════════════════════
    # Configure
    # ══════════════════════════════════════════════════════════════════════════

    def configure_scan(self, settings: ScanSettings) -> None:
        with self._lock:
            if self._is_scanning:
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
                settings=settings,
                constraints=self._constraints,
            )

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
                settings=default_back,
                constraints=self._constraints,
            )

    def configure_back_scan(self, settings: ScanSettings) -> None:
        """
        Accept and store back scan settings.

        The PI E-710 only gates photon triggers during the forward sweep.
        We accept the settings so the logic and GUI work normally, but
        get_back_scan_data() will always return NaN-filled arrays.
        """
        with self._lock:
            if self._is_scanning:
                self.log.error('Cannot configure back scan while scanning is in progress.')
                return
            if self._scan_settings is None:
                self.log.error('Configure forward scan before configuring back scan.')
                return

            self._constraints.check_back_scan_settings(settings, self._scan_settings)

            self._back_scan_settings = settings
            self._back_scan_data = ScanData.from_constraints(
                settings=settings,
                constraints=self._constraints,
            )

    # ══════════════════════════════════════════════════════════════════════════
    # Motion  —  fully delegated to scanner
    # ══════════════════════════════════════════════════════════════════════════

    def move_absolute(
        self,
        position: Dict[str, float],
        velocity: Optional[float] = None,
        blocking: bool = False,
    ) -> Dict[str, float]:
        if self._is_scanning:
            self.log.error('Cannot move while scan is in progress.')
            return self.get_target()
        return self._scanner().move_absolute(position, blocking=blocking)

    def move_relative(
        self,
        distance: Dict[str, float],
        velocity: Optional[float] = None,
        blocking: bool = False,
    ) -> Dict[str, float]:
        if self._is_scanning:
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
            if self._is_scanning:
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
            self._is_scanning = True

            self._scan_thread = threading.Thread(
                target=self._scan_worker,
                name='PIE710CounterScanWorker',
                daemon=True,
            )
            self._scan_thread.start()

    def stop_scan(self) -> None:
        if not self._is_scanning:
            self.log.warning('stop_scan() called but no scan is running.')
            return

        self._stop_event.set()
        self._scanner().halt_generators()
        self._counter().stop()

        self._is_scanning = False

        if self._scan_thread and self._scan_thread.is_alive():
            self._scan_thread.join(timeout=10.0)

        self.log.info('Scan stopped.')

    def get_scan_data(self) -> Optional[ScanData]:
        return self._scan_data

    def get_back_scan_data(self) -> Optional[ScanData]:
        """
        Returns NaN-filled ScanData.
        The PI E-710 only gates photon triggers on the forward sweep.
        """
        return self._back_scan_data

    def emergency_stop(self) -> None:
        self._stop_event.set()
        self._scanner().halt()
        self._scanner().halt_generators()
        self._counter().stop()
        self._is_scanning = False
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
        """
        try:
            s       = self._scan_settings
            t_pixel = 1.0 / s.frequency

            # Build position arrays for each scanned axis
            positions = tuple(
                np.linspace(r[0], r[1], n).tolist()
                for r, n in zip(s.range, s.resolution)
            )

            # Total pixels: product of all axis resolutions
            n_total = int(np.prod(s.resolution))

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

            # ── 2. Start scanner waveform (non-blocking) ──────────────────
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
            self._store_data(
                counts_dict = counts_dict,
                settings    = s,
                t_pixel     = t_pixel,
            )

            # Update target position tracking from sensor readout
            self._scanner().sync_position()

        except Exception as exc:
            self.log.exception(f'Scan worker unhandled exception: {exc}')
        finally:
            self._is_scanning = False

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

        1D:  data shape  (n_x,)
        2D:  counter delivers samples in row-major order (line by line):
                 index k = i_slow * n_fast + i_fast
             reshape (n_slow, n_fast) → transpose → (n_fast, n_slow)
             to match Qudi ScanData convention: data[i_fast, i_slow]
        """
        if self._scan_data is None:
            return

        if counts_dict is None:
            self.log.warning('counts_dict is None — scan data will remain NaN.')
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
                        f'Count array length mismatch '
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
                        f'Count array length mismatch '
                        f'(got {len(raw)}, expected {n_total}). Filling zeros.'
                    )
                return np.zeros((n_fast, n_slow), dtype=float)

        data_dict = {ch: _to_rate(counts_dict.get(ch)) for ch in channels}

        try:
            self._scan_data.data = data_dict
        except ValueError as exc:
            self.log.error(f'ScanData write failed: {exc}')