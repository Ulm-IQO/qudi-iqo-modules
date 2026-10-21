# -*- coding: utf-8 -*-

"""
Writer for synthetic PicoQuant Generic-T2 PTU files.

snAPI can open a PTU file as a "file device" (``snAPI.getFileDevice``) and replay it through the
normal measurement classes. That makes it possible to exercise
``qudi.hardware.picoquant.snapi_fast_counter`` end to end without a real harp: generate a file with
a known trigger/photon pattern here, point the module's ``ptu_file`` config option at it, and check
that the resulting histogram is the expected one.

Run as a script to produce a demo file for a qudi dry run::

    python tests/synthetic_ptu.py C:\\Data\\PicoQuant\\qudi_dryrun.ptu

The record layout follows PicoQuant's own reference reader
(https://github.com/PicoQuant/PicoQuant-Time-Tagged-File-Format-Demos, PTU/Python/Read_PTU.py):
a Generic-T2 record is 32 bit little endian with 1 bit "special", 6 bit channel and 25 bit timetag.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-iqo-modules/>

This file is part of qudi.

Qudi is free software: you can redistribute it and/or modify it under the terms of
the GNU Lesser General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version.

Qudi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along with qudi.
If not, see <https://www.gnu.org/licenses/>.
"""

import struct
import sys

# PTU tag type codes
_TY_EMPTY8 = 0xFFFF0008
_TY_BOOL8 = 0x00000008
_TY_INT8 = 0x10000008
_TY_FLOAT8 = 0x20000008
_TY_TDATETIME = 0x21000008
_TY_ANSISTRING = 0x4001FFFF

# Record type of MultiHarp / PicoHarp 330 T2 data
_RT_GENERIC_T2 = 0x00010207
# The 25 bit timetag field wraps after this many base-resolution ticks.
_T2_WRAPAROUND = 33554432

# Special-record channel number that flags a timetag overflow.
_OVERFLOW_CHANNEL = 0x3F

#: Measurement mode value written into the PTU header for T2 data.
MEAS_MODE_T2 = 2


def _tag(ident, tag_type, value, idx=-1):
    """ Encode a single PTU header tag. """
    head = ident.encode('ascii').ljust(32, b'\0') + struct.pack('<i', idx)
    head += struct.pack('<I', tag_type)
    if tag_type in (_TY_INT8, _TY_BOOL8):
        return head + struct.pack('<q', int(value))
    if tag_type in (_TY_FLOAT8, _TY_TDATETIME):
        return head + struct.pack('<d', float(value))
    if tag_type == _TY_EMPTY8:
        return head + b'\0' * 8
    if tag_type == _TY_ANSISTRING:
        raw = value.encode('ascii') + b'\0'
        raw += b'\0' * (-len(raw) % 8)  # PTU pads strings to a multiple of 8 bytes
        return head + struct.pack('<q', len(raw)) + raw
    raise ValueError(f'Unsupported PTU tag type {tag_type:#x}')


def encode_records(events, base_resolution_ps=5.0):
    """ Encode time-ordered events into Generic-T2 records, inserting overflow records as needed.

    @param list events: ``(time_ps, kind, number)`` tuples, sorted by time. ``kind`` is one of
                        'sync', 'marker' (number 1..4) or 'photon' (number = snAPI channel 1..N)
    @param float base_resolution_ps: device base resolution; event times must be a multiple of it

    @return list: 32 bit integer records
    """
    records = []
    overflow_ticks = 0
    previous_ticks = -1

    for time_ps, kind, number in events:
        ticks = time_ps / base_resolution_ps
        if ticks != int(ticks):
            raise ValueError(
                f'Event time {time_ps} ps is not a multiple of the base resolution '
                f'{base_resolution_ps} ps and cannot be represented exactly.'
            )
        ticks = int(ticks)
        if ticks < previous_ticks:
            raise ValueError('Events must be passed in ascending time order.')
        previous_ticks = ticks

        while ticks - overflow_ticks >= _T2_WRAPAROUND:
            records.append((1 << 31) | (_OVERFLOW_CHANNEL << 25) | 1)
            overflow_ticks += _T2_WRAPAROUND

        timetag = ticks - overflow_ticks
        if kind == 'sync':
            special, channel = 1, 0
        elif kind == 'marker':
            if not 1 <= number <= 4:
                raise ValueError('marker number must be 1..4')
            special, channel = 1, number
        elif kind == 'photon':
            if number < 1:
                raise ValueError('photon channel must be >= 1 (0 is the sync input)')
            # snAPI channel n corresponds to record channel n-1
            special, channel = 0, number - 1
        else:
            raise ValueError(f'Unknown event kind {kind!r}')

        records.append((special << 31) | (channel << 25) | timetag)

    return records


def write_ptu(path, events, number_of_channels=4, base_resolution_ps=5.0,
              model='MultiHarp 150', serial='01234567'):
    """ Write a synthetic Generic-T2 PTU file that snAPI can open as a file device.

    @param str path: destination file path
    @param list events: see :func:`encode_records`
    @param int number_of_channels: number of input channels reported in the header
    @param float base_resolution_ps: device base resolution in picoseconds
    @param str model: hardware model string reported by snAPI as ``deviceConfig['Model']``
    @param str serial: hardware serial number written into the header

    @return int: number of records written
    """
    records = encode_records(events, base_resolution_ps)

    # snAPI reads the base resolution from the header in seconds and reports it in picoseconds.
    base_resolution_s = base_resolution_ps * 1e-12
    tags = b''.join([
        _tag('File_GUID', _TY_ANSISTRING, '{00000000-0000-0000-0000-000000000000}'),
        _tag('File_AssuredContent', _TY_ANSISTRING, 'PQTTTR'),
        _tag('CreatorSW_Name', _TY_ANSISTRING, 'qudi synthetic_ptu'),
        _tag('CreatorSW_Version', _TY_ANSISTRING, '1.0'),
        _tag('File_CreatingTime', _TY_TDATETIME, 45000.0),
        _tag('Measurement_Mode', _TY_INT8, MEAS_MODE_T2),
        _tag('Measurement_SubMode', _TY_INT8, 0),
        _tag('TTResultFormat_TTTRRecType', _TY_INT8, _RT_GENERIC_T2),
        _tag('TTResultFormat_BitsPerRecord', _TY_INT8, 32),
        _tag('MeasDesc_BinningFactor', _TY_INT8, 1),
        _tag('MeasDesc_Offset', _TY_INT8, 0),
        _tag('MeasDesc_AcquisitionTime', _TY_INT8, 1000),
        _tag('MeasDesc_StopAt', _TY_INT8, 0),
        _tag('MeasDesc_StopOnOvfl', _TY_BOOL8, 0),
        _tag('MeasDesc_Restart', _TY_BOOL8, 0),
        _tag('MeasDesc_Resolution', _TY_FLOAT8, base_resolution_s),
        _tag('MeasDesc_GlobalResolution', _TY_FLOAT8, 1e-12),
        _tag('HW_Type', _TY_ANSISTRING, model),
        _tag('HW_PartNo', _TY_ANSISTRING, '930043'),
        _tag('HW_Version', _TY_ANSISTRING, '1.0'),
        _tag('HW_SerialNo', _TY_ANSISTRING, serial),
        _tag('HW_Modules', _TY_INT8, 1),
        _tag('HW_BaseResolution', _TY_FLOAT8, base_resolution_s),
        _tag('HW_InpChannels', _TY_INT8, number_of_channels),
        _tag('HW_ExternalRefClock', _TY_BOOL8, 0),
        _tag('TTResult_SyncRate', _TY_INT8, 1000000),
        _tag('TTResult_StopAfter', _TY_INT8, 1000),
        _tag('TTResult_StopReason', _TY_INT8, 0),
        _tag('TTResult_NumberOfRecords', _TY_INT8, len(records)),
        _tag('Header_End', _TY_EMPTY8, 0),
    ])

    with open(path, 'wb') as file:
        file.write(b'PQTTTR\0\0')
        file.write(b'00.0.1\0\0')
        file.write(tags)
        file.write(struct.pack(f'<{len(records)}I', *records))

    return len(records)


def gated_sequence(number_of_gates=5, sweeps=20, period_ps=5000, gate_delay_ps=100,
                   photon_channel=1, trigger_kind='sync', trigger_number=0):
    """ Build a pulsed-sequence event list: one photon per trigger, delayed by the gate index.

    Gate ``g`` puts its photon at ``(g + 1) * gate_delay_ps`` after its trigger, so the resulting
    2D trace has exactly one populated bin per gate row and the peak walks across the rows.
    """
    events = []
    time_ps = 0
    for sweep in range(sweeps):
        for gate in range(number_of_gates):
            events.append((time_ps, trigger_kind, trigger_number))
            events.append((time_ps + (gate + 1) * gate_delay_ps, 'photon', photon_channel))
            time_ps += period_ps
    return events


def decay_sequence(sweeps=2000, period_ps=5000, lifetime_ps=800, photon_channel=1, seed=42):
    """ Build an ungated lifetime event list: one exponentially delayed photon per sync pulse. """
    import random

    rng = random.Random(seed)
    events = []
    time_ps = 0
    for sweep in range(sweeps):
        events.append((time_ps, 'sync', 0))
        # Quantise to the 5 ps base resolution grid and keep the photon inside the sync period.
        delay_ps = min(int(rng.expovariate(1.0 / lifetime_ps)) // 5 * 5, period_ps - 5)
        events.append((time_ps + delay_ps, 'photon', photon_channel))
        time_ps += period_ps
    return events


if __name__ == '__main__':
    destination = sys.argv[1] if len(sys.argv) > 1 else 'qudi_dryrun.ptu'
    written = write_ptu(destination, decay_sequence())
    print(f'Wrote {written} Generic-T2 records to {destination}')
