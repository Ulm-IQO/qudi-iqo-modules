# -*- coding: utf-8 -*-
"""
Read-only archiver: every `interval` s, save every xy / z scan in qudi's scan history
(scanning_data_logic, only the last ~20 are kept there) that is not yet saved, as
<folder>/scan_<timestamp>_<axes>.npz (data per channel, range, resolution, axes,
timestamp). Changes nothing in qudi. Stops when <folder>/STOP exists or after
max_hours.

    python archive_scans.py [folder] [interval_s] [max_hours]
"""
import os
import sys
import time

import numpy as np
import rpyc

folder = sys.argv[1] if len(sys.argv) > 1 else 'WireFieldMapping_scans'
interval = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0
max_hours = float(sys.argv[3]) if len(sys.argv) > 3 else 48.0
os.makedirs(folder, exist_ok=True)
log = open(os.path.join(folder, 'archive_log.txt'), 'a', encoding='utf-8')


def note(msg):
    log.write(time.strftime('%Y-%m-%d %H:%M:%S ') + msg + '\n')
    log.flush()


t_end = time.time() + max_hours * 3600
note(f'started (interval {interval} s)')
while time.time() < t_end and not os.path.exists(os.path.join(folder, 'STOP')):
    try:
        conn = rpyc.connect('localhost', 18861, config={'allow_all_attrs': True, 'allow_pickle': True,
                                                        'sync_request_timeout': 120})
        sdl = conn.root.get_namespace_dict()['scanning_data_logic']
        for entry in list(sdl._scan_history):
            sd = entry[0] if isinstance(entry, (tuple, list)) else entry
            if sd.data is None:
                continue
            axes = tuple(str(a) for a in sd.settings.axes)
            ts = sd.timestamp.strftime('%Y%m%d_%H%M%S_%f')
            path = os.path.join(folder, 'scan_{0}_{1}.npz'.format(ts, ''.join(axes)))
            if os.path.exists(path):
                continue
            data = {f'data_{k}': np.array(rpyc.classic.obtain(v), float) for k, v in sd.data.items()}
            np.savez(path, axes=np.array(axes), timestamp=sd.timestamp.isoformat(),
                     range=np.array([[float(r[0]), float(r[1])] for r in sd.settings.range]),
                     resolution=np.array([int(r) for r in sd.settings.resolution]), **data)
            note(f'saved {os.path.basename(path)}')
        conn.close()
    except Exception as exc:
        note(f'error: {exc!r}')
    for _ in range(int(interval)):
        if os.path.exists(os.path.join(folder, 'STOP')):
            break
        time.sleep(1)
note('stopped')
