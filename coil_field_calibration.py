# -*- coding: utf-8 -*-
"""
coil_field_calibration.py

Use the coil calibration written by the 2026-10-05 coil characterization
(CoilCalibration/coil_calibration_2026-10-05.json):

    B = M @ I + B0

B is the field at the NV ensemble in the NV quantization frame (the frame of
`quantization_axes`, x/y/z closest to the X/Y/Z coil axes), I the PHYSICAL
coil currents (A; see the relay note below). Standalone: numpy only
(predicted_odmr_lines also needs nothing else).

    import coil_field_calibration as cfc
    cal = cfc.load_coil_calibration()
    B = cfc.field_from_currents([1.0, -0.5, 2.0], cal, units='G')
    I = cfc.currents_for_field([30.0, 0.0, 10.0], cal, units='G')
    f = cfc.predicted_odmr_lines(I, cal)            # 8 ODMR lines, Hz

Relay note: in the 2026-10-04/05 calibration data, the polarity relay
commanded as 'X' reversed the Y coil and vice versa (config `coil_control`:
psu_x/psu_y were cross-connected, switch_names were not). The calibration is
in physical coil currents. switch_names was changed to x: 'Y', y: 'X' on
2026-10-05, so commanded currents = physical currents from then on.
`commanded_for_physical()` is only needed with the old config (or to
reinterpret older data).

Symmetry note: ODMR spectra are invariant under the 48 symmetries of the four
NV axes, so the calibration fixes B only up to one of them; the stored M is
the one whose coil directions are closest to nominal x, y, z. Predicted
spectra (and fields relative to the NV axes) are unaffected by that choice.
"""

import json
import os

import numpy as np

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'CoilCalibration',
                            'coil_calibration_2026-10-05.json')
_UNIT_PER_MHZ = {'MHz': 1.0, 'Hz': 1e6, 'G': None}


def load_coil_calibration(path=DEFAULT_PATH):
    """The calibration dict, with numpy arrays 'M' (MHz/A, 3x3, columns X Y Z)
    and 'B0' (MHz) added."""
    with open(path, 'r', encoding='utf-8') as fh:
        cal = json.load(fh)
    cal['M'] = np.array(cal['M_MHz_per_A'], dtype=float)
    cal['B0'] = np.array(cal['B0_MHz'], dtype=float)
    return cal


def _to_mhz(B, units, cal):
    B = np.asarray(B, dtype=float)
    if units == 'G':
        return B * cal['gamma_MHz_per_G']
    return B / _UNIT_PER_MHZ[units]


def _from_mhz(B, units, cal):
    if units == 'G':
        return B / cal['gamma_MHz_per_G']
    return B * _UNIT_PER_MHZ[units]


def field_from_currents(I, cal, units='MHz'):
    """Field (NV frame) for physical coil current vector(s) I (A), shape (3,)
    or (N, 3). units: 'MHz' (gamma * B), 'Hz' or 'G'."""
    I = np.asarray(I, dtype=float)
    B = I @ cal['M'].T + cal['B0']
    return _from_mhz(B, units, cal)


def currents_for_field(B, cal, units='MHz', check_limits=True):
    """Physical coil currents (A) that produce field B (NV frame; shape (3,)
    or (N, 3)): I = M^-1 (B - B0). Raises if a current exceeds the range the
    calibration was measured over (cal['valid_current_range_A']) unless
    check_limits=False."""
    Bm = _to_mhz(B, units, cal)
    I = np.linalg.solve(cal['M'], (np.atleast_2d(Bm) - cal['B0']).T).T
    if check_limits:
        lim = np.array([cal['valid_current_range_A'][c] for c in 'XYZ'])
        if np.any(np.abs(I) > lim + 1e-9):
            raise ValueError(f'currents {np.round(I, 4).tolist()} A exceed the calibrated range '
                             f'{lim.tolist()} A (check_limits=False to override)')
    return I[0] if np.ndim(B) == 1 else I


def predicted_odmr_lines(I, cal, D=None):
    """The 8 ODMR transition frequencies (Hz, sorted) for physical currents I
    (A): spin-1 Hamiltonian D Sz^2 + gamma B.S for every NV axis (E = 0)."""
    D = cal['D_MHz'] if D is None else D
    B = field_from_currents(I, cal, units='MHz')
    s2 = 1 / np.sqrt(2)
    Sx = s2 * np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]], dtype=complex)
    Sy = s2 * np.array([[0, -1j, 0], [1j, 0, -1j], [0, 1j, 0]], dtype=complex)
    Sz = np.diag([1.0, 0.0, -1.0]).astype(complex)
    S = np.array([Sx, Sy, Sz])
    lines = []
    for a in cal['quantization_axes']:
        a = np.asarray(a, dtype=float) / np.linalg.norm(a)
        Sa = np.tensordot(a, S, 1)
        ev = np.linalg.eigvalsh(D * Sa @ Sa + np.tensordot(B, S, 1))
        lines += [ev[1] - ev[0], ev[2] - ev[0]]
    return np.sort(np.array(lines)) * 1e6


def commanded_for_physical(I_phys, tiny=1e-3):
    """OLD CONFIG ONLY (switch_names x: 'X', y: 'Y', before 2026-10-05; not
    needed with x: 'Y', y: 'X'). Setpoints for coil_control_logic that give
    physical currents I_phys while the X/Y polarity relays are swapped: the 'X'
    setpoint carries the X magnitude with the Y coil's sign (it drives the
    relay of the Y coil), and vice versa. A coil at 0 A whose partner needs a
    given polarity gets `tiny` A so that its setpoint can carry the sign
    (a 0 A setpoint does not move the relay)."""
    I = np.asarray(I_phys, dtype=float)
    sx = np.sign(I[0]) if I[0] != 0 else 1.0
    sy = np.sign(I[1]) if I[1] != 0 else 1.0
    mx = abs(I[0]) if I[0] != 0 or I[1] == 0 else tiny
    my = abs(I[1]) if I[1] != 0 or I[0] == 0 else tiny
    return np.array([mx * sy, my * sx, I[2]])
