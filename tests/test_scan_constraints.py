import pytest

from qudi.util.constraints import ScalarConstraint
from qudi.interface.scanning_probe_interface import ScanConstraints, ScannerAxis, ScannerChannel, ScanSettings


@pytest.fixture
def scan_constraints():
    axis_1 = ScannerAxis(
        name='x',
        unit='m',
        position=ScalarConstraint(default=0, bounds=(-100, 100)),
        step=ScalarConstraint(default=0.1, bounds=(-100, 100)),
        resolution=ScalarConstraint(default=128, bounds=(8, 256), enforce_int=True),
        frequency=ScalarConstraint(default=10, bounds=(10, 100), enforce_int=True),
    )
    axis_2 = ScannerAxis(
        name='y',
        unit='m',
        position=ScalarConstraint(default=0, bounds=(-100, 100)),
        step=ScalarConstraint(default=0.1, bounds=(-100, 100)),
        resolution=ScalarConstraint(default=128, bounds=(8, 256), enforce_int=True),
        frequency=ScalarConstraint(default=10, bounds=(10, 100), enforce_int=True),
    )
    ch = ScannerChannel(name='APD', unit='Hz')
    return ScanConstraints(
        channel_objects=(ch,),
        axis_objects=(axis_1, axis_2),
        backscan_configurable=False,
        has_position_feedback=False,
        square_px_only=False,
    )


@pytest.fixture
def valid_settings():
    return ScanSettings(
        channels=('APD',),
        axes=('x', 'y'),
        range=((0.0, 100.0), (0.0, 100.0)),
        resolution=(128, 8),
        frequency=50,
    )


def test_is_valid(scan_constraints, valid_settings):
    assert scan_constraints.is_valid(valid_settings)


def test_check_settings(scan_constraints, valid_settings):
    scan_constraints.check_settings(valid_settings)


def test_check_channels_fail(scan_constraints):
    settings = ScanSettings(
        channels=('yo',),
        axes=('x', 'y'),
        range=((0.0, 100.0), (0.0, 100.0)),
        resolution=(128, 8),
        frequency=50,
    )
    with pytest.raises(ValueError) as e:
        scan_constraints.check_settings(settings)
    assert 'Unknown channel' in str(e.value)


def test_check_axes_name_fail(scan_constraints):
    settings = ScanSettings(
        channels=('APD',),
        axes=('x', 'z'),
        range=((0.0, 100.0), (0.0, 100.0)),
        resolution=(128, 8),
        frequency=50,
    )
    with pytest.raises(ValueError) as e:
        scan_constraints.check_settings(settings)
    assert 'Unknown axis' in str(e.value)


def test_check_axes_value_fail(scan_constraints):
    settings = ScanSettings(
        channels=('APD',),
        axes=('x', 'y'),
        range=((0.0, 100000.0), (0.0, 100.0)),
        resolution=(128, 8),
        frequency=50,
    )
    with pytest.raises(ValueError) as e:
        scan_constraints.check_settings(settings)
    assert 'Scan range out of bounds' in str(e.value)
