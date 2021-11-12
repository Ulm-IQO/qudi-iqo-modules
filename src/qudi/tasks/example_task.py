# -*- coding: utf-8 -*-
"""
Exemplary tasks to demonstrate interaction with other iqo-modules.

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

from typing import Iterable, Optional, Tuple

from qudi.core.scripting.moduletask import ModuleTask
from qudi.core.connector import Connector

class ExampleTask(ModuleTask):
    """
    Example config:

    taskrunnerlogic:
        module.Class: taskrunner.TaskRunnerLogic
        module_tasks:
            test_task:
                module.Class: qudi.tasks.example_task.ExampleTask
                connect:
                    pulsedmasterlogic: pulsed_master_logic
    """

    pulsedmasterlogic = Connector(name='pulsedmasterlogic', interface='PulsedMasterLogic')

    def _setup(self) -> None:
        generator = self.pulsedmasterlogic().sequencegeneratorlogic()

        generator.generate_predefined_sequence(predefined_sequence_name='laser_on', kwargs_dict={})
        generator.sample_pulse_block_ensemble('laser_on')
        generator.load_ensemble('laser_on')


    def _run(self, run_arg=0) -> None:
        self.log.info(f"Laser_on loaded, now ready for task. Received param run_arg= {run_arg}")


    def _cleanup(self) -> None:
        self.log.info("Task finshed.")


class ExampleTask2(ModuleTask):
    """
    Example config:

    taskrunnerlogic:
       module.Class: taskrunner.TaskRunnerLogic
       module_tasks:
           test_task:
               module.Class: qudi.tasks.example_task.ExampleTask2
    """

    def _setup(self) -> None:
        pass

    def _cleanup(self) -> None:
        pass

    def _run(self, iter_arg: Iterable[str], opt_arg: Optional[int] = 42
             ) -> Tuple[Iterable[str], int]:

        self.log.info(f"Received iter_arg= {iter_arg}, opt_arg= {opt_arg}")

        return iter_arg, opt_arg
