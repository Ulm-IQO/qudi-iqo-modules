# -*- coding: utf-8 -*-

"""
This file contains the compiler that turns single-qubit Qiskit circuits into microwave pulse
descriptions for the qudi pulsed toolchain. It has no qudi dependencies, so it can be tested and
used without a running qudi instance.

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

__all__ = [
    'AreaMatch',
    'CompiledCircuit',
    'DEFAULT_GATE_STRING',
    'DEFAULT_PYTHON_CIRCUIT',
    'DEFAULT_PYTHON_CIRCUIT_ONE_LINE',
    'GatePulse',
    'NATIVE_BASIS_GATES',
    'SingleQubitGate',
    'TEMPLATE_NAMES',
    'build_template_circuit',
    'circuit_from_gate_string',
    'circuit_from_python_source',
    'compile_circuit',
    'parse_angle',
    'transpile_to_native',
]

import ast
import operator
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Mapping, Tuple

import numpy as np

# Gates the compiler emits directly. Every circuit is transpiled into this basis first.
NATIVE_BASIS_GATES = ('rx', 'ry', 'rz')
# Instructions that carry no rotation. They are skipped without touching the rotating frame.
IGNORED_INSTRUCTIONS = ('barrier', 'id', 'delay')
# Rotations smaller than this are dropped instead of being emitted as a zero length pulse.
ANGLE_EPSILON = 1e-9
# Gates accepted in a gate string. Anything outside the native basis is transpiled afterwards.
GATE_STRING_ANGLE_GATES = ('rx', 'ry', 'rz', 'p')
GATE_STRING_PLAIN_GATES = ('x', 'y', 'z', 'h', 's', 'sdg', 't', 'tdg', 'sx', 'sxdg', 'id')
# Circuit templates offered by the predefined generate methods (generate_qiskit_<template>).
TEMPLATE_NAMES = ('gate', 'ramsey_virtual_z', 'xy4', 'null_sequence', 'gate_string', 'python_circuit')

DEFAULT_GATE_STRING = 'rx(pi/2); rz(pi/2); rx(pi/2)'
DEFAULT_PYTHON_CIRCUIT = (
    "# Build any single-qubit circuit. The variable named 'circuit' is compiled.\n"
    'circuit = QuantumCircuit(1)\n'
    'circuit.rx(pi / 2, 0)\n'
    'circuit.rz(pi / 2, 0)  # virtual Z: no pulse, shifts the phase of every later pulse\n'
    'circuit.rx(pi / 2, 0)\n'
)
# The same circuit on one line, for parameter fields that cannot hold line breaks.
DEFAULT_PYTHON_CIRCUIT_ONE_LINE = (
    'circuit = QuantumCircuit(1); circuit.rx(pi/2, 0); circuit.rz(pi/2, 0); circuit.rx(pi/2, 0)'
)

_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {ast.USub: operator.neg, ast.UAdd: operator.pos}


class AreaMatch(Enum):
    """
    How a change of the pulse envelope is compensated so that the rotation angle is preserved.
    The rotation angle is set by the pulse area, so a shaped pulse with the same peak and length as
    a rectangular one under-rotates by the mean height of its envelope.
    """

    duration = 'duration'  # stretch every pulse by the reciprocal of the mean envelope height
    amplitude = 'amplitude'  # raise the peak amplitude instead, if the pulse generator has headroom
    none = 'none'  # no compensation, every rotation angle is scaled by the mean envelope height


class SingleQubitGate(Enum):
    """
    Single-qubit gates offered as ready-made circuits. The value is the gate in gate string form.
    """

    x = 'x'
    y = 'y'
    z = 'z'
    h = 'h'
    s = 's'
    sdg = 'sdg'
    t = 't'
    tdg = 'tdg'
    sx = 'sx'
    sxdg = 'sxdg'
    x90 = 'rx(pi/2)'
    y90 = 'ry(pi/2)'
    x_minus_90 = 'rx(-pi/2)'
    y_minus_90 = 'ry(-pi/2)'


@dataclass(frozen=True)
class GatePulse:
    """
    One microwave pulse that implements one rotation gate.
    """

    index: int  # position of the instruction in the transpiled circuit
    gate: str  # 'rx' or 'ry'
    angle: float  # rotation angle in rad, in [0, pi]
    phase: float  # drive phase in degrees, in [0, 360)

    @property
    def pi_fraction(self) -> float:
        """Length of the pulse relative to a pi pulse."""
        return self.angle / np.pi

    @property
    def label(self) -> str:
        """Short description of the pulse, e.g. for logs and previews."""
        return f'{self.gate}({self.pi_fraction:.3g} pi) at {self.phase:.1f} deg'


@dataclass
class CompiledCircuit:
    """
    A transpiled single-qubit circuit together with the pulses that implement it.
    The emitted pulses reproduce the circuit unitary up to a final rotation about Z by
    -frame_phase, which is unobservable in a Z basis readout, and up to a global phase.
    """

    circuit: Any  # the transpiled qiskit.QuantumCircuit
    pulses: List[GatePulse] = field(default_factory=list)
    frame_phase: float = 0.0  # phase of the rotating frame in degrees after the last gate

    @property
    def drawing(self) -> str:
        """Text drawing of the transpiled circuit."""
        drawing = self.circuit.draw(output='text')
        # Force UTF-8, otherwise the drawer falls back to the console encoding and warns on Windows.
        drawing.encoding = 'utf-8'
        return str(drawing)

    @property
    def gate_count(self) -> int:
        """Number of gates in the transpiled circuit."""
        return len(self.circuit.data)


def _qiskit():
    """Import qiskit on first use, so this module can be imported without it."""
    try:
        import qiskit
    except ImportError as err:
        raise ImportError(
            f'The Qiskit pulsed toolchain needs the "qiskit" package, which could not be imported ({err}). '
            f'Install it with "pip install qiskit".'
        ) from err
    return qiskit


def _evaluate_angle_node(node):
    """Evaluate one node of an angle expression. Only numbers, pi and arithmetic are accepted."""
    if isinstance(node, ast.Expression):
        return _evaluate_angle_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id.lower() == 'pi':
        return np.pi
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        return _BINARY_OPERATORS[type(node.op)](_evaluate_angle_node(node.left), _evaluate_angle_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate_angle_node(node.operand))
    raise ValueError(f'"{ast.unparse(node)}" is not allowed in an angle. Use numbers, pi and the operators + - * / **.')


def parse_angle(expression: str) -> float:
    """
    Evaluate an angle expression such as "pi/2", "-3*pi/4" or "90deg".

    The angle is in radians unless it ends with "deg". Only numbers, the name "pi" and the
    arithmetic operators + - * / ** are accepted, so nothing else can be executed through it.

    @param str expression: the angle expression
    @return float: the angle in radians
    """
    text = str(expression).strip()
    degrees = text.lower().endswith('deg')
    if degrees:
        text = text[:-3].strip()
    try:
        tree = ast.parse(text, mode='eval')
    except SyntaxError:
        raise ValueError(f'Could not read "{expression}" as an angle.') from None
    try:
        value = _evaluate_angle_node(tree)
    except ZeroDivisionError:
        raise ValueError(f'The angle "{expression}" divides by zero.') from None
    return float(np.radians(value) if degrees else value)


def _split_gate_string(text: str) -> List[str]:
    """Split a gate string at semicolons, line breaks and commas outside of brackets."""
    tokens, current, depth = list(), list(), 0
    for character in str(text):
        if character == '(':
            depth += 1
        elif character == ')':
            depth = max(0, depth - 1)
        if character in ';\n' or (character == ',' and depth == 0):
            tokens.append(''.join(current))
            current = list()
        else:
            current.append(character)
    tokens.append(''.join(current))
    return [token.strip() for token in tokens if token.strip()]


def circuit_from_gate_string(text: str):
    """
    Build a single-qubit circuit from a gate string.

    Gates are separated by semicolons, commas or line breaks. Gates taking an angle are written
    with the angle in brackets, for example "rx(pi/2)" or "rz(90deg)". Gates without an angle are
    written by name, for example "h" or "t". Gates outside the native basis are transpiled later,
    so the string may use any gate listed in GATE_STRING_ANGLE_GATES and GATE_STRING_PLAIN_GATES.

    @param str text: the gate string
    @return qiskit.QuantumCircuit: the circuit
    """
    QuantumCircuit = _qiskit().QuantumCircuit
    tokens = _split_gate_string(text)
    if not tokens:
        raise ValueError('The gate string is empty.')
    circuit = QuantumCircuit(1)
    for token in tokens:
        match = re.fullmatch(r'([A-Za-z_][A-Za-z_0-9]*)\s*(?:\(\s*(.*?)\s*\))?', token)
        if match is None:
            raise ValueError(f'Could not read "{token}" as a gate.')
        gate, argument = match.group(1).lower(), match.group(2)
        if gate in GATE_STRING_ANGLE_GATES:
            if not argument:
                raise ValueError(f'Gate "{gate}" needs an angle, as in {gate}(pi/2).')
            getattr(circuit, gate)(parse_angle(argument), 0)
        elif gate in GATE_STRING_PLAIN_GATES:
            if argument:
                raise ValueError(f'Gate "{gate}" does not take an argument.')
            getattr(circuit, gate)(0)
        else:
            raise ValueError(
                f'Gate "{gate}" is not supported. Gates with an angle: {", ".join(GATE_STRING_ANGLE_GATES)}. '
                f'Gates without an angle: {", ".join(GATE_STRING_PLAIN_GATES)}.'
            )
    return circuit


def circuit_from_python_source(source: str):
    """
    Run Python source that builds a QuantumCircuit and return the circuit.

    The source runs with QuantumCircuit, np, numpy and pi predefined; anything else can be imported
    inside it. The circuit assigned to the variable "circuit" is used. If there is no such variable
    but exactly one QuantumCircuit was created, that one is used instead.

    @param str source: Python source code
    @return qiskit.QuantumCircuit: the circuit
    """
    qiskit = _qiskit()
    namespace = {'QuantumCircuit': qiskit.QuantumCircuit, 'np': np, 'numpy': np, 'pi': np.pi}
    exec(compile(str(source), '<qiskit circuit>', 'exec'), namespace)
    circuit = namespace.get('circuit')
    if isinstance(circuit, qiskit.QuantumCircuit):
        return circuit
    circuits = [value for value in namespace.values() if isinstance(value, qiskit.QuantumCircuit)]
    if len(circuits) == 1:
        return circuits[0]
    raise ValueError('The Python source has to assign a QuantumCircuit to a variable named "circuit".')


def _single_qubit_gate(gate) -> SingleQubitGate:
    """Accept a SingleQubitGate member (also as a remote proxy), its name or its value."""
    if isinstance(gate, SingleQubitGate):
        return gate
    text = str(getattr(gate, 'value', gate))
    try:
        return SingleQubitGate[text]
    except KeyError:
        return SingleQubitGate(text)


def build_template_circuit(template: str, parameters: Mapping[str, Any]):
    """
    Build the circuit of one of the ready-made templates.

    @param str template: template name, one of TEMPLATE_NAMES
    @param dict parameters: the template parameters, as passed to the generate method
    @return qiskit.QuantumCircuit: the circuit
    """
    QuantumCircuit = _qiskit().QuantumCircuit
    if template == 'gate':
        return circuit_from_gate_string(_single_qubit_gate(parameters.get('gate', SingleQubitGate.x)).value)
    if template == 'ramsey_virtual_z':
        # pi/2, a virtual Z, then pi/2. The population left in |0> follows cos^2(phase/2), so a
        # sweep of the phase gives a fringe and confirms that rz reaches the drive phase.
        circuit = QuantumCircuit(1)
        circuit.rx(np.pi / 2, 0)
        circuit.rz(np.radians(float(parameters.get('phase_deg', 90.0))), 0)
        circuit.rx(np.pi / 2, 0)
        return circuit
    if template == 'xy4':
        # X, Y, X, Y, all pi pulses: the XY4 decoupling block.
        circuit = QuantumCircuit(1)
        for axis in ('rx', 'ry', 'rx', 'ry'):
            getattr(circuit, axis)(np.pi, 0)
        return circuit
    if template == 'null_sequence':
        # Equal to the identity, so the spin ends where it started. Exercises negative angles.
        circuit = QuantumCircuit(1)
        circuit.rx(np.pi / 2, 0)
        circuit.ry(-np.pi / 2, 0)
        circuit.ry(np.pi / 2, 0)
        circuit.rx(-np.pi / 2, 0)
        return circuit
    if template == 'gate_string':
        return circuit_from_gate_string(parameters.get('gates', DEFAULT_GATE_STRING))
    if template == 'python_circuit':
        return circuit_from_python_source(parameters.get('source', DEFAULT_PYTHON_CIRCUIT))
    raise ValueError(f'Unknown circuit template "{template}". Known templates: {", ".join(TEMPLATE_NAMES)}.')


def transpile_to_native(circuit):
    """
    Transpile a circuit into the native basis. Optimisation level 0 keeps the emitted pulses in
    one-to-one correspondence with the gates as written.

    @param qiskit.QuantumCircuit circuit: the circuit
    @return qiskit.QuantumCircuit: the transpiled circuit
    """
    return _qiskit().transpile(circuit, basis_gates=list(NATIVE_BASIS_GATES), optimization_level=0)


def compile_circuit(circuit) -> CompiledCircuit:
    """
    Transpile a single-qubit circuit and turn every rotation into a pulse description.

    A rotation about an axis in the equatorial plane becomes a pulse whose drive phase is the axis
    angle (rx: 0 deg, ry: 90 deg) and whose area is the rotation angle. An rz gate emits no pulse
    and instead shifts the phase of every later pulse, following
    R_phi(theta) RZ(lambda) = RZ(lambda) R_(phi - lambda)(theta). Angles are wrapped into
    (-pi, pi] and a negative angle adds 180 degrees to the phase, since R_phi(-theta) equals
    R_(phi + 180deg)(theta). No pulse is therefore longer than a pi pulse.

    @param qiskit.QuantumCircuit circuit: a circuit on one qubit
    @return CompiledCircuit: the transpiled circuit and its pulses
    """
    if circuit.num_qubits != 1:
        raise NotImplementedError(
            f'The circuit has {circuit.num_qubits} qubits, but only one spin on one microwave channel is '
            f'driven. Multi-qubit gates need a second driven spin and a native two-qubit interaction.'
        )
    if any(instruction.operation.name == 'measure' for instruction in circuit.data):
        raise NotImplementedError(
            'Measurements are not supported. Remove the measure instructions; the laser pulse after the '
            'gates is the readout.'
        )
    transpiled = transpile_to_native(circuit)
    compiled = CompiledCircuit(circuit=transpiled)
    frame_phase = 0.0
    for index, instruction in enumerate(transpiled.data):
        name = instruction.operation.name
        if name in IGNORED_INSTRUCTIONS:
            continue
        if len(instruction.qubits) != 1:
            raise NotImplementedError(f'Instruction {index} ("{name}") acts on {len(instruction.qubits)} qubits.')
        if name not in NATIVE_BASIS_GATES:
            raise NotImplementedError(
                f'Instruction {index} ("{name}") could not be transpiled into {", ".join(NATIVE_BASIS_GATES)}.'
            )
        try:
            theta = float(instruction.operation.params[0])
        except (IndexError, TypeError) as err:
            raise ValueError(
                f'Instruction {index} ("{name}") has no numeric rotation angle. Bind all circuit parameters first.'
            ) from err
        if name == 'rz':
            frame_phase -= np.degrees(theta)
            continue
        angle, sign_phase = _wrap_angle(theta)
        if angle < ANGLE_EPSILON:
            continue
        axis_phase = 90.0 if name == 'ry' else 0.0
        phase = (axis_phase + sign_phase + frame_phase) % 360.0
        compiled.pulses.append(GatePulse(index=index, gate=name, angle=angle, phase=phase))
    compiled.frame_phase = frame_phase % 360.0
    return compiled


def _wrap_angle(theta: float) -> Tuple[float, float]:
    """
    Wrap an angle into (-pi, pi] and return its magnitude and the drive phase its sign adds.

    @param float theta: rotation angle in rad
    @return tuple(float, float): magnitude in [0, pi], additional drive phase in degrees (0 or 180)
    """
    wrapped = float(theta) % (2 * np.pi)
    if wrapped > np.pi:
        wrapped -= 2 * np.pi
    return abs(wrapped), (180.0 if wrapped < 0 else 0.0)
