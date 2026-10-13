# -*- coding: utf-8 -*-
"""
View-only live mirror of the Keysight MXR604A screen (USB or LAN) on this PC: polls a
PNG screenshot (:DISPlay:DATA? PNG, ~2.7 s per frame over USB) and shows it in a
matplotlib window. Changes nothing on the scope. Close the window or Ctrl+C to stop.

    python scope_view.py [visa_address] [interval_s]

Also: save_screenshot('file.png') for a single capture.
"""
import io
import sys
import time

import matplotlib.pyplot as plt
import pyvisa

SCOPE_ADDRESS = 'TCPIP0::192.168.2.2::inst0::INSTR'   # LAN (USB: 'USB0::0x2A8D::0x9007::MY60120264::INSTR')


def _open(address=SCOPE_ADDRESS):
    scope = pyvisa.ResourceManager().open_resource(address)
    scope.timeout = 20000
    return scope


def grab_png(scope):
    return scope.query_binary_values(':DISPlay:DATA? PNG', datatype='B', container=bytes)


def save_screenshot(path, address=SCOPE_ADDRESS):
    scope = _open(address)
    try:
        with open(path, 'wb') as fh:
            fh.write(grab_png(scope))
    finally:
        scope.close()


def view(address=SCOPE_ADDRESS, interval=0.0):
    scope = _open(address)
    plt.ion()
    fig, ax = plt.subplots(figsize=(13, 7.5))
    fig.canvas.manager.set_window_title('MXR604A')
    ax.axis('off')
    fig.subplots_adjust(0, 0, 1, 1)
    im = None
    try:
        while plt.fignum_exists(fig.number):
            t = time.time()
            img = plt.imread(io.BytesIO(grab_png(scope)), format='png')
            if im is None:
                im = ax.imshow(img)
            else:
                im.set_data(img)
            fig.canvas.draw_idle()
            plt.pause(max(0.05, interval - (time.time() - t)))
    except KeyboardInterrupt:
        pass
    finally:
        scope.close()


if __name__ == '__main__':
    view(sys.argv[1] if len(sys.argv) > 1 else SCOPE_ADDRESS,
         float(sys.argv[2]) if len(sys.argv) > 2 else 0.0)
