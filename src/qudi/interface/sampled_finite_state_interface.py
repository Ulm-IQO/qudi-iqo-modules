from typing import Iterable, Union, Tuple, Callable
import time
from PySide2 import QtCore
from qudi.util.mutex import Mutex
from fysom import Fysom, FysomError

from qudi.core.module import Base
from qudi.core.configoption import ConfigOption

def state(method: Callable) -> Callable:
   """Decorator for marking a method as a watchog state.

   The method should not expect any parameter other than `self`.

   Example:
   ```
   class MyClass(SampledFiniteStateInterface):
       @state
       def say_hi(self):
           print("hi")
   ```
   """
   method._fysom_state = True
   return method

def initial(method: Callable) -> Callable:
   """Decorator for marking a state as the initial state.

   Example:
   ```
   class MyClass(SampledFiniteStateInterface):
       @state
       @initial
       def say_hi(self):
           print("hi")
   ```
   """
   method._fysom_is_initial = True
   return method

def transition_to(*args: Tuple[str, str]) -> Callable:
   """Decorator for listing the allowed transitions from a given watchdog state (the decorated method).

   Example:
   ```
   class MyClass(SampledFiniteStateInterface):
       @state
       @transition_to(("hi", "say_hi), ("bar", "say_bar"))
       def say_foo(self):
           print("foo")
       @state
       @transition_to(("foo", "say_foo"), ("hi", "say_hi"))
       def say_bar(self):
           print("bar")
       @state
       @transition_to(("foo", "say_foo"), ("bar", "say_bar"))
       def say_hi(self):
           print("hi")
   ```
   """
   def decorator(func: Callable) -> Callable:
       rules = getattr(func, "_fysom_transition_to", [])
       rules.extend(args)
       func._fysom_transition_to = rules
       return func
   return decorator

def transition_from(*args: Tuple[str, Union[str, Iterable[str]]]) -> Callable:
   """Decorator for listing the allowed transitions to a given watchdog state (the decorated method).

   # Example:
   ```
   class MyClass(SampledFiniteStateInterface):
       @state
       @transition_to(("hi", "say_hi), ("bar", "say_bar"))
       def say_foo(self):
           print("foo")
       @state
       @transition_to(("foo", "say_foo"), ("hi", "say_hi"))
       def say_bar(self):
           print("bar")
       @state
       @transition_to(("foo", "say_foo"), ("bar", "say_bar"))
       def say_hi(self):
           print("hi")
       @state
       @transition_from(("sink", "*"))
       def sink_event(self):
           print("sank.")
   ```
   """
   def decorator(func: Callable) -> Callable:
       rules = getattr(func, "_fysom_transition_from", [])
       rules.extend(args)
       func._fysom_transition_from = rules
       return func
   return decorator

class SampledFiniteStateInterface(Base):
    """
    An interface to simplify using a fysom finite state machine to control a process.

    You can mark states and the associated transitions using the `@state`, `@transition_from`, and `@transition_to` decorators.

    An example usage is given in hardware/dummy/excitation_scanner_dummy.py.

    This will define a watchdog that will be polled each `self._watchdog_delay` seconds (this is a `ConfigOption`) and call the appropriate method for the current state. You can transition between state using the `watchdog_event` method.

    Remember to activate and start the watchdog in your `on_activate` method, and
    to deactivate it in the `on_deactivate` method.

    """
    _watchdog_delay = ConfigOption(name="watchdog_delay", default=0.2)
    sig_on_event = QtCore.Signal(str)
    sig_on_state = QtCore.Signal(str)
    sig_start_watchdog = QtCore.Signal()
    sig_stop_watchdog = QtCore.Signal()

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        states = []
        events_structure = []
        events = set()
        initial = None
        for name, val in cls.__dict__.items():
            transitions_from = getattr(val, "_fysom_transition_from", [])
            transitions_to = getattr(val, "_fysom_transition_to", [])
            if getattr(val, "_fysom_state", False):
                states.append(name)
            if getattr(val, "_fysom_is_initial", False):
                initial = name
            for (event, from_state) in transitions_from:
                events_structure.append(dict(name=event, src=from_state, dst=name))
                events.add(event)
            for (event, to_state) in transitions_to:
                events_structure.append(dict(name=event, src=name, dst=to_state))
                events.add(event)
        if initial is not None:
            cls._initial_state = initial
        cls.__fysom_events_structure = events_structure
        cls.__fysom_events = events
        cls.__fysom_states = states
        if "_initial_state" not in cls.__dict__.keys():
            raise NotImplementedError("Subclasses of SampledFiniteStateInterface must have an `_initial_state` string attribute or declare a state as initial using `@initial`.")
        for event in events:
            setattr(cls, "sig_on_event_" + event, QtCore.Signal())
        for state in states:
            setattr(cls, "sig_on_state_" + state, QtCore.Signal())

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._watchdog_timer = QtCore.QTimer(parent=self)
        self._watchdog_lock = Mutex()
        self._watchdog_running = False
        callbacks_structure = {}
        for event in self.__fysom_events:
            specific_callback = getattr(self, "sig_on_event_" + event).emit
            callbacks_structure["on_" + event] = lambda _: (specific_callback(), self.sig_on_event.emit(event))
        for state in self.__fysom_states:
            specific_callback = getattr(self, "sig_on_state_" + state).emit
            callbacks_structure["on_" + state] = lambda _: (specific_callback(), self.sig_on_state.emit(state))
        self._watchdog_state = Fysom({
            'initial': self._initial_state,
            'events': self.__fysom_events_structure,
            'callbacks': callbacks_structure,
        })

    def __watchdog(self):
        time_start = time.perf_counter()
        watchdog_state = self.watchdog_state
        try:
            callback = getattr(self, watchdog_state)
            callback()
        except FysomError:
            self.log.error("There is an error in your Fysom state machine! stopping the watchdog now.")
            self._watchdog_running = False
            self.log.exception("")
        except:
            self.log.exception("")
        finally:
            if self._watchdog_running:
                time_end = time.perf_counter()
                time_overhead = time_end-time_start
                new_time = max(0, self._watchdog_delay - time_overhead)
                self._watchdog_timer.start(new_time*1000)

    @property
    def watchdog_state(self) -> str:
        """Query the current state of the watchdog."""
        with self._watchdog_lock:
            return self._watchdog_state.current

    def watchdog_event(self, event) -> None:
        "Trigger a watchdog event that must have beed declared beforehand."
        with self._watchdog_lock:
            self._watchdog_state.trigger(event)

    def enable_watchdog(self) -> None:
        "Enable the watchdog mechanism."
        self._watchdog_timer.setSingleShot(True)
        self._watchdog_timer.timeout.connect(self.__watchdog, QtCore.Qt.QueuedConnection)
        self.sig_start_watchdog.connect(self.__start_watchdog, QtCore.Qt.QueuedConnection)
        self.sig_stop_watchdog.connect(self.__stop_watchdog, QtCore.Qt.QueuedConnection)

    def disable_watchdog(self) -> None:
        "Disable the watchdog mechanism. Will interrupt the watchdog if necessary."
        if self._watchdog_running:
            self.stop_watchdog()
        self._watchdog_timer.timeout.disconnect()
        self.sig_start_watchdog.disconnect(self.__start_watchdog)
        self.sig_stop_watchdog.disconnect(self.__stop_watchdog)

    def __start_watchdog(self) -> None:
        self._watchdog_running = True
        self._watchdog_timer.start(self._watchdog_delay * 1000)

    def __stop_watchdog(self) -> None:
        self._watchdog_running = False

    def start_watchdog(self) -> None:
        "Start the watchdog. The mechanism must have been enabled beforehand."
        self.sig_start_watchdog.emit()

    def stop_watchdog(self) -> None:
        "Interrupt the watchdog."
        self.sig_stop_watchdog.emit()

