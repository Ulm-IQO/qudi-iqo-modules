# -*- coding: utf-8 -*-

"""
This file contains unit tests for the scanning optimize logic module.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-core/>

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

import time
import pytest

SCANNER_GUI = "scanner_gui"
LOGIC_MODULE = 'scanning_optimize_logic'


def get_scan_logic(opt_module):
    """
    Return the linked *ScanningProbeLogic* instance for the optimisation logic.

    Parameters
    ----------
    opt_module : object
        module instance for optimize logic
    """
    return opt_module._scan_logic()


def wait_until(predicate, timeout=5, delay=0.05):
    """
    Utility: poll *predicate* until it returns *True* or *timeout* expires.
    Parameters
    ----------
    predicate : function object
        function that return boolean value
    timeout : float
        waiting time for predicate
    delay : float
        small delays between steps in the while loop
    """
    start = time.time()
    while time.time() - start < timeout:
        if predicate():
            return True
        time.sleep(delay)
    return False


@pytest.fixture(scope="module")
def module(qudi_client):
    """
    Return an activated *ScanningOptimizeLogic* instance living in the remote Qudi.
    Parameters
    ----------
    qudi_client : fixture
        qudi kernel client
    """
    mm = qudi_client.module_manager

    mm.activate_module(SCANNER_GUI)

    return mm._modules[LOGIC_MODULE].instance



def test_start_optimize(module):
    """
    Verify that *start_optimize()* kicks off an optimisation sequence correctly.
    Parameters
    ----------
    module : fixture
        logic module instance
    """
    # start from a clean state.
    if module.optimizer_running:
        module.stop_optimize()
        wait_until(lambda: not module.optimizer_running, timeout=2)

 
    # Start optimisation.
    module.start_optimize()

    # The optimiser should switch to *running* almost instantly.
    assert wait_until(lambda: module.optimizer_running, timeout=2), "Optimizer did not start."

  
    # stashed settings must be captured so they can be
    # restored when optimisation stops.
    assert module._stashed_settings is not None, "Original scan settings were not stashed."

    # Wait for the optimiser to finish its configured sequence automatically.
    assert wait_until(lambda: not module.optimizer_running, timeout=20), "Optimisation did not finish in time."

 
    # Stashed settings are cleared once they have been reapplied.
    assert module._stashed_settings is None, "Stashed settings were not cleared after optimise run."

    # Results should contain at least one scan and a corresponding fit.
    assert len(module.last_scans) > 0, "No scans recorded during optimisation."
    assert len(module.last_scans) == len(module.last_fits), "Mismatch between scans and fits."

    # The optimiser should have determined at least one axis position.
    assert module.optimal_position, "Optimal position dict is empty."


def test_stop_optimize(module):
    """
    Check that *stop_optimize()* cleanly aborts an ongoing optimisation and
    restores the prior scanner state.
    Parameters
    ----------
    module : fixture
        logic module instance
    """
    # It should be idle before starting this test.
    if module.optimizer_running:
        module.stop_optimize()
        wait_until(lambda: not module.optimizer_running, timeout=2)

    # Capture current scanner settings so we can compare later.
    scan_logic = get_scan_logic(module)
    original_settings = scan_logic.get_scan_settings_per_ax()

    # Kick off optimisation.
    module.start_optimize()
    assert wait_until(lambda: module.optimizer_running, timeout=2)

    # The optimiser is running now ⇒ *save_to_history* should be *False*.
    assert not scan_logic.save_to_history

    # Abort optimisation.
    module.stop_optimize()
    assert wait_until(lambda: not module.optimizer_running, timeout=5), "Optimizer did not stop."  # noqa: E501

    # After stopping, history saving must be re‑enabled.
    assert scan_logic.save_to_history, "Scan history flag was not restored after stopping."

    # Stashed settings must be discarded
    assert module._stashed_settings is None

    # Scanner settings need to be identical to what we had before starting.
    restored_settings = scan_logic.get_scan_settings_per_ax()
    assert restored_settings == original_settings, "Scanner settings were not fully restored."
