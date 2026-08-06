# `pulsed_data` — typed containers for the pulsed toolchain

This package holds the dataclasses that the three pulsed logic modules use to store their settings,
their measurement data, and the metadata attached to generated pulse objects. Before this package
existed, all of it was loose dictionaries and ~20 separately-declared `StatusVar`s per module; a typo
in a settings key was silently accepted and a missing key in a saved file could reset every setting
you had.

**If you are here to add a lab-specific global generation parameter, you only need one file:**
[`generation_parameter_extensions.py`](generation_parameter_extensions.py). Skip to
[Extending this](#extending-this--notes-for-physicists).

There is no `__init__.py` in this folder. That is deliberate and matches the rest of `qudi/logic/`
(they are implicit namespace packages), so everything is imported by its full module path:

```python
from qudi.logic.pulsed.pulsed_data.sequence_generator_logic_data import GenerationParameters
```

---

## The six files at a glance

| File | Owns the data for | Classes |
|---|---|---|
| [`generation_parameter_extensions.py`](generation_parameter_extensions.py) | **Your lab's** extra generation parameters | 2 |
| [`sequence_generator_logic_data.py`](sequence_generator_logic_data.py) | `SequenceGeneratorLogic` — generation parameters, pulser hardware mirror, sampling results | 14 |
| [`pulsed_measurement_logic_data.py`](pulsed_measurement_logic_data.py) | `PulsedMeasurementLogic` — microwave, fast counter, readout, live measurement state | 13 |
| [`pulsed_measurement.py`](pulsed_measurement.py) | The top-level snapshot of one complete measurement | 4 |
| [`pulsed_master_logic_data.py`](pulsed_master_logic_data.py) | `PulsedMasterLogic` — busy flags, fit containers | 2 |
| [`settings_coercion.py`](settings_coercion.py) | Shared helper every settings setter calls | 1 class + 2 functions |

---

## Read this first — five conventions

Almost every oddity in these files follows from one of these five rules. Learning them here saves
reading three dozen class docstrings.

### 1. `frozen=True` means the object can never be changed

```python
@dataclass(frozen=True)
class MicrowaveSettings:
    power: float
    frequency: float
    use_ext_microwave: bool
```

`frozen=True` makes Python refuse `settings.power = -20`. To "change" a value you build a **new**
object with `dataclasses.replace()`, which copies everything and overrides only what you name:

```python
from dataclasses import replace
new_settings = replace(old_settings, power=-20.0)   # old_settings is untouched
```

**Why bother?** Three concrete reasons, all of which bit the old dict-based code:

- These objects are read from more than one Qt thread. An immutable object cannot be half-updated
  while another thread is reading it.
- They get pickled into every saved `.ensemble`/`.sequence` file. If they were mutable, a later edit
  in the GUI would retroactively change what a "saved" snapshot means.
- A settings object handed to a caller cannot be corrupted by that caller.

Some classes here are deliberately **not** frozen. See rule 4.

When using a jupyter notebook, it is adviced to change frozen settings dataclasses using their setters, which accept 4 different input formats:
-Keywoard Arguments
-Plain dict (must be compatible)
-The dataclass itself

All setters use update_from_dict or replace, which will always create a brand new clean object.

### 2. Every persisted class implements the same three methods

| Method | Direction | Missing keys behave as | Called by |
|---|---|---|---|
| `to_dict(self)` | object → plain dict | — | saving to the status file; the GUI; measurement metadata headers |
| `from_dict(cls, data)` | dict → **new** object | fall back to the **field default** | restoring at module activation |
| `update_from_dict(self, data)` | dict → **new** object, patched onto `self` | **keep the current value** | every live edit from the GUI or a script |

`from_dict` is a `@classmethod` — it is called on the class (`MicrowaveSettings.from_dict({...})`)
because there is no instance yet; that is why its first argument is `cls` and not `self`.

The difference between the last two is the whole point:

```python
current = MicrowaveSettings(power=-30.0, frequency=2.87e9, use_ext_microwave=True)

MicrowaveSettings.from_dict({'power': -20.0})   # frequency → its DEFAULT
current.update_from_dict({'power': -20.0})      # frequency → stays 2.87e9
```

So `from_dict` is for *"rebuild this from a file"* and `update_from_dict` is for *"the user just moved
one slider"*.

### 3. `from_dict()` must tolerate keys that are not there

A status file written last year does not contain a field added last week. If `from_dict` indexed
`data['new_field']` directly it would raise `KeyError` — and because the `StatusVar` constructor
catches that and falls back to the default *object*, **every** setting in that file would silently
revert. That is a real bug this package was written to kill; see the docstring on
`_generation_parameters_from_dict` in [`sequence_generator_logic_data.py`](sequence_generator_logic_data.py).

The rule for anyone adding a field: use `data.get('name', fallback)` or an
`if 'name' in data else default` guard, never `data['name']`, and **ignore** unknown keys rather than
rejecting them, so that removing a lab extension does not make its old status file unloadable.

The two aggregate classes go one step further and fall back per *sub-object*:
`PulsedMeasurementSettings.from_dict()` and `SequenceGeneratorSettings.from_dict()` substitute the
shared `_DEFAULT_*` instances for whole missing sections.

### 4. Some classes are mutable **on purpose** — do not "fix" them

| Class | Why it is not frozen |
|---|---|
| `AnalysisParameters`, `ExtractionParameters` | `PulseAnalyzer`/`PulseExtractor` hold **this exact object** and mutate it in place as their live state |
| `AlternativeSignalSettings` | same, for `AltPlotAnalyzer` |
| `PulsedMeasurementData` | the measurement loop writes into these arrays on every analysis tick |
| `ExecutionState`, `DataStashCache`, `SequenceSamplingState` | per-run scratch state, never persisted |
| `MeasurementInformation`, `SamplingInformation`, `GenerationMethodParameters` | live **on** a pulse object and are written by the generate/sample step |
| `PulsedMeasurement`, `PulsedData`, `PulseObjects` | freezing the container would not freeze the mutable things inside it, only imply it misleadingly |

The consequence shows up in `PulsedMeasurementSettings.update_from_dict()`: for the shared ones it
calls `.update(...)` **in place** and never rebinds the reference, because rebinding would silently
disconnect `PulseAnalyzer` from the settings the rest of the app thinks it is using.

### 5. `class X(dict)` is a compatibility shim, not a mistake

Five classes subclass `dict` instead of being dataclasses:
`AnalysisParameters`, `ExtractionParameters`, `GenerationMethodParameters`,
`MetadataDictRepresentation`, `PulsedMasterStatus`.

They exist where old call sites index and assign directly and were not worth rewriting:

```python
# pulsed_maingui.py does this, and still can:
pulsedmasterlogic().status_dict['benchmark_busy'] = True
# while PulsedMasterLogic itself gets typo-proof named access to the same data:
self.status_dict.benchmark_busy = True
```

**Gotcha this creates:** for these classes `isinstance(x, dict)` is `True`. Any code that branches on
"is this a dict or a settings object?" must check the specific class **first**. `coerce_settings()`
does exactly that, and says so in a comment.

---

## Hierarchy

### Containment — what holds what

`PulsedMeasurement` is the root of everything that describes one measurement.

```
PulsedMeasurement                                   [mutable]  pulsed_measurement.py
│
├── settings : Settings                             [frozen]
│   │
│   ├── measurement_settings : PulsedMeasurementSettings          [frozen]
│   │   ├── microwave_settings        : MicrowaveSettings         [frozen]
│   │   ├── fast_counter_settings     : FastCounterSettings       [frozen]
│   │   ├── readout_settings          : ReadoutSettings           [frozen]
│   │   ├── alternate_signal_settings : AlternativeSignalSettings (shared, mutable)
│   │   ├── extraction_parameters     : ExtractionParameters      (dict subclass, shared)
│   │   └── analysis_parameters       : AnalysisParameters        (dict subclass, shared)
│   │
│   └── generator_settings : SequenceGeneratorSettings | None     [frozen]
│       ├── generation_parameters     : GenerationParameters      [frozen, BUILT AT IMPORT]
│       │   ├── ... CoreGenerationParameters' 14 fields
│       │   └── ... every lab extension's fields
│       └── pulse_generator_settings  : PulseGeneratorSettings    [frozen]
│           ├── activation_config     : ActivationConfig          [frozen]
│           ├── analog_levels         : AnalogLevels              [frozen]
│           ├── digital_levels        : DigitalLevels             [frozen]
│           └── sample_rate / interleave / flags / upload_speed
│
├── data : PulsedData | None                        [mutable]
│   ├── measurement_data : PulsedMeasurementData    [mutable]   raw/laser/signal arrays
│   ├── fit_result       : lmfit ModelResult | None             (one-way export only)
│   └── fit_result_alt   : lmfit ModelResult | None             (one-way export only)
│
└── objects : PulseObjects                          [mutable]
    ├── sequence  : PulseBlockEnsemble | PulseSequence | None    ← lives in pulse_objects.py
    ├── ensembles : {name: PulseBlockEnsemble}                   independent copies
    └── blocks    : {name: PulseBlock}                           independent copies
```

Each pulse object in `objects` carries three more containers from this package, attached as plain
attributes rather than nested here:

```
PulseBlockEnsemble / PulseSequence          (pulse_objects.py)
├── .sampling_information          : SamplingInformation           [mutable, dict-like]
├── .measurement_information       : MeasurementInformation        [mutable, dict-like]
└── .generation_method_parameters  : GenerationMethodParameters    (dict subclass)
```

Standing on their own, not part of the tree above:

```
SequenceGeneratorLogic                          PulsedMeasurementLogic       PulsedMasterLogic
├── pulser_benchmarks : PulserBenchmarks        ├── __execution_state :      ├── status_dict :
│   ├── write : BenchmarkTool                   │   ExecutionState           │   PulsedMasterStatus
│   └── load  : BenchmarkTool                   └── _data_stash :            └── fit_containers :
├── (returned) EnsembleAnalysisResult               DataStashCache               FitContainers
├── (returned) SequenceAnalysisResult                                            (property — a new
├── (returned) AssetInfo, LoadedAsset                                             one per access)
└── (per-run)  SequenceSamplingState
```

### Inheritance — there is almost none, and that is on purpose

These are containers, not a class hierarchy. Two exceptions:

```
BaseGenerationParameters                    generation_parameter_extensions.py
├── CoreGenerationParameters                sequence_generator_logic_data.py  (built-in, 14 fields)
├── TestParameters                          generation_parameter_extensions.py (example, 1 field)
└── <your lab's class here>                 generation_parameter_extensions.py
        │
        └──► merged at import time by _build_generation_parameters()
             into ONE class:  GenerationParameters
             (a real subclass of all of the above, with all of their fields)

dict
├── AnalysisParameters             ┐
├── ExtractionParameters           │ compatibility shims — see convention 5
├── GenerationMethodParameters     │
├── PulsedMasterStatus             │
└── MetadataDictRepresentation     ┘ (pretty-printing wrapper only)
```

---

## File-by-file catalogue

### `generation_parameter_extensions.py`

**This is the file to edit** when your lab needs an extra global generation setting. Everything else
adapts automatically: a widget appears on the Predefined Methods tab, the value is saved to and
restored from the status file, and predefined generate methods can read it.

#### `BaseGenerationParameters` — the marker base class

Declares no fields. Anything inheriting from it is collected and merged into `GenerationParameters`.
Provides three helpers to subclasses:

| Member | What it does |
|---|---|
| `_coerce_fields(cls, data, current=None)` | **Already implemented — you normally do not touch it.** Returns `{field_name: value}` for this class's own fields only, reading from `data`, else `current`, else the default, and converting each value according to the type its field declares. |
| `_own_field_names(cls)` | The names this class itself declared (not inherited ones). Used for the duplicate-name check, for `to_dict()` ordering, and to drive the coercion loop. |
| `_pick(data, current, name, default)` | The 3-step fallback: `data` → `current` → `default`. |

`_coerce_fields` backs **both** `from_dict` (called with `current=None`, so missing keys become
defaults) and `update_from_dict` (called with `current=self`, so missing keys keep their value). That
is why the per-field conversion is expressed exactly once.

The conversion it applies comes from the field's declared type, so declaring the field *is* declaring
how it is restored:

| Declared type | Conversion applied to the saved value |
|---|---|
| `int`, `float`, `str` | `int(value)`, `float(value)`, `str(value)` |
| `bool` | `as_bool(value)` — `'False'`/`'no'`/`'off'`/`'0'` are False. A plain `bool('False')` is `True`, which is why this exists |
| an `Enum` subclass | looked up by member name, else by member value |
| any other class | an instance passes through as-is; a `dict` goes through that class's `from_dict()` if it has one — this is how `PulseEnvelope` round-trips |

A value that cannot be converted (a corrupted status file) is **logged and skipped**, and that one
field falls back to its current value, else its default. It deliberately does not raise: the
`StatusVar` constructor swallows an exception and returns the whole default object, so one bad key
would otherwise silently reset every generation parameter — convention 3 again.

Type detection cannot know that a fraction must stay within [0, 1]. For a field like that, override
`_coerce_fields`, let `super()` handle everything else, and patch only the key concerned:

```python
@classmethod
def _coerce_fields(cls, data, current=None):
    coerced = super()._coerce_fields(data, current)
    coerced['laser_power_fraction'] = min(1.0, max(0.0, coerced['laser_power_fraction']))
    return coerced
```

`_own_field_names` intersects two sources. `cls.__dict__['__annotations__']` gives ownership and
declaration order — plain `cls.__annotations__` and `dataclasses.fields()` both walk up to parent
classes and would return inherited fields too. `dataclasses.fields()` then gives membership, which is
what drops `ClassVar`/`InitVar` declarations: those are annotations but **not** fields, so without the
intersection a lab constant like `CHANNEL_MAP: ClassVar[dict] = {...}` would be exported by
`to_dict()` as though it were a measurement parameter, compared by the duplicate-name check, and
passed to the merged constructor as an unexpected keyword argument.

**Why this class lives here and not next to `CoreGenerationParameters`:** so this file never has to
import from `sequence_generator_logic_data.py`. A circular import between the two would make the merge
silently skip extensions depending on which module happened to be imported first.

#### `TestParameters` — a live example

```python
@dataclass(frozen=True)
class TestParameters(BaseGenerationParameters):
    time_delay: float = 4.0

    @classmethod
    def _coerce_fields(cls, data, current=None):
        pick = cls._pick
        return {'time_delay': float(pick(data, current, 'time_delay', 4.0))}
```

> **Note:** this is currently active, so `time_delay` really is a field of `GenerationParameters` and
> really does get a spin box on the Predefined Methods tab. It is scaffolding from the refactor — if
> your setup does not want it, delete the class.
>
> Its `_coerce_fields` predates the automatic version and does exactly what the base class would now
> do for a `float` field. **Do not copy it into a new class** — the two lines of `time_delay: float =
> 4.0` are the whole modern form.

---

### `sequence_generator_logic_data.py`

Everything `SequenceGeneratorLogic` stores: what pulses to build, what hardware to build them on, and
what came out of building them.

#### `CoreGenerationParameters` *(frozen)* — the built-in generation settings

| Field | Type | Default | Meaning |
|---|---|---|---|
| `laser_channel` | str | `'d_ch1'` | channel that triggers the laser |
| `sync_channel` | str | `''` | optional sync-out channel, empty = unused |
| `gate_channel` | str | `''` | gate channel for a gated fast counter, empty = ungated |
| `microwave_channel` | str | `'a_ch1'` | channel carrying the MW pulses |
| `microwave_frequency` | float | `2.87e9` | Hz — NV zero-field splitting by default |
| `microwave_amplitude` | float | `0.0` | V |
| `rabi_period` | float | `100e-9` | s — one full Rabi cycle; π-pulses are derived from it |
| `laser_length` | float | `3e-6` | s — readout laser pulse duration |
| `laser_delay` | float | `500e-9` | s — delay between laser trigger and actual light |
| `wait_time` | float | `1e-6` | s — relaxation wait after readout |
| `analog_trigger_voltage` | float | `0.0` | V |
| `optimal_control_assets_path` | str | `C:\Software\qudi_data\optimal_control_assets` | where OC pulse files live |
| `pulse_envelope` | `PulseEnvelope` | rectangle | pulse shape (see `sampling_functions.py`) |
| `pulse_envelope_order` | int | `1` | shape order parameter |

**This class is not the type the rest of the toolchain uses** — that is the merged
`GenerationParameters` below. `CoreGenerationParameters` is one *contributor* to it.

#### `GenerationParameters` *(frozen, built at import time)*

Not written out as a class. It is constructed by `_build_generation_parameters()` at the bottom of the
module, which:

1. collects `CoreGenerationParameters` plus every `BaseGenerationParameters` subclass currently
   declared, sorted by class name so the result never depends on import order;
2. raises `TypeError` if two contributors declare the same field name — a silent last-wins would
   change a measurement parameter with no other signal;
3. builds the merged class with `type('GenerationParameters', contributors, {})`;
4. attaches `_CONTRIBUTORS` (the tuple of classes) and the three dict methods.

`to_dict()` walks `_CONTRIBUTORS` rather than `dataclasses.fields()`, because a merged dataclass lays
its fields out in reverse-MRO order — extension fields would come out *before* the built-in ones, and
the GUI builds the Predefined Methods widget grid by iterating this dict in order.

Two constraints on the merged class that are easy to break accidentally:

- **Every field needs a default.** Python forbids a no-default field following a defaulted one, and
  after merging you cannot control the ordering.
- **The result must be bound to the module-level name `GenerationParameters`**, matching the class's
  own `__name__`, so that `pickle` can resolve it. A `GenerationParameters` instance is pickled into
  every saved `.ensemble`/`.sequence` via `SamplingInformation`.

Accessed everywhere through `SequenceGeneratorLogic.generation_parameters`, which returns
`.to_dict()` — so most call sites see a plain dict:

```python
self.generation_parameters['rabi_period']                 # predefined generate methods
self.generator_settings.generation_parameters.rabi_period # the dataclass directly
```

#### `ActivationConfig` *(frozen)*, `AnalogLevels` *(frozen)*, `DigitalLevels` *(frozen)*

Thin named wrappers over what used to be anonymous tuples.

| Class | Fields | Also has |
|---|---|---|
| `ActivationConfig` | `name: str`, `channels: set` | `as_tuple`, `from_tuple()` |
| `AnalogLevels` | `amplitude: dict`, `offset: dict` | `as_tuple`, `from_tuple()` |
| `DigitalLevels` | `low: dict`, `high: dict` | `as_tuple`, `from_tuple()` |

The `as_tuple`/`from_tuple` pair exists because `PulserInterface` still speaks in tuples — these
convert at the boundary without forcing every hardware module to change.

#### `PulseGeneratorSettings` *(frozen)* — the local mirror of the pulser hardware

| Field | Type | Meaning |
|---|---|---|
| `activation_config` | `ActivationConfig` | which channels are switched on |
| `sample_rate` | float | Sa/s |
| `analog_levels` | `AnalogLevels` | pp-amplitude and offset per analog channel |
| `digital_levels` | `DigitalLevels` | low/high voltage per digital channel |
| `interleave` | bool | interleave mode |
| `flags` | set | pulser flag names |
| `upload_speed` | float | Sa/s, from the benchmark |

Properties `analog_channels` / `digital_channels` filter `activation_config.channels` by the
`a_ch`/`d_ch` prefix.

Mirrored locally because reading it back from hardware is slow. `SequenceGeneratorLogic.on_activate()`
re-derives it from hardware on every activation, which is why it is *not* migrated from legacy status
files — there would be no point.

#### `AssetInfo` *(frozen)* and `LoadedAsset` *(frozen)*

| Class | Fields | Purpose |
|---|---|---|
| `AssetInfo` | `length_s`, `length_bins`, `number_of_lasers` | summary of a sampled ensemble/sequence |
| `LoadedAsset` | `name`, `asset_type` | what is currently on the pulser; `asset_type` is `'PulseBlockEnsemble'`, `'PulseSequence'` or `''` |

Both implement `__iter__`/`__len__`/`__getitem__` so old tuple-style call sites still work
(`*loaded_asset`, `loaded_asset[0]`). `LoadedAsset` also defines `__bool__` — falsy unless *both* a
name and a recognized type are set — and a `LoadedAsset.empty()` constructor.

#### `SequenceSamplingState` *(mutable)* — per-run scratch space

Created fresh for each `PulseSequence` sampling run and thrown away when it ends, successfully or not.
Never persisted.

| Field | Purpose |
|---|---|
| `in_progress` | set by `start()` / cleared by `finish()` |
| `offset_bin` | running sample offset across steps |
| `written_waveforms` | every waveform name written so far this run |
| `generated_ensembles` | per-step ensemble info, keyed by name tag |
| `step_results` | `(waveform_names, seq_step)` per step |

#### `PulserBenchmarks` *(mutable)* — upload/load speed telemetry

Holds two `BenchmarkTool` instances (`write`, `load`) and combines them:

```python
1 / (1/write_speed + 1/load_speed)     # → estimate_combined_speed(), NaN if not enough data
```

Persisted as its **own independent** `StatusVar` on `SequenceGeneratorLogic`, deliberately *not*
inside `SequenceGeneratorSettings` — it is runtime telemetry about the hardware, not a
measurement-defining setting, and should not appear in a saved measurement's settings.

#### `EnsembleAnalysisResult` *(frozen)* and `SequenceAnalysisResult` *(frozen)*

Pure return values, never persisted, produced by `SequenceGeneratorLogic.analyze_block_ensemble()`
and `analyze_sequence()`.

`EnsembleAnalysisResult`: `number_of_samples`, `number_of_elements`, `elements_length_bins`,
`digital_rising_bins`, `digital_falling_bins`, `analog_channels`, `digital_channels`, `channel_set`,
`generation_parameters`, `ideal_length`, `laser_rising_bins`, `laser_falling_bins`.

`SequenceAnalysisResult`: the same information aggregated over a whole sequence, plus per-step
breakdowns (`number_of_steps`, `number_of_samples_per_step`, `number_of_ensembles`, `ensemble_names`,
`number_of_elements_per_step`, `elements_length_bins_per_step`, `ideal_length_per_step`).

Both carry the `generation_parameters` in force at analysis time, which is how those end up in a saved
measurement's metadata.

#### `SamplingInformation` *(mutable, dict-like)*

Attached to every `PulseBlockEnsemble`/`PulseSequence` as `.sampling_information`. Records what
happened when that object was sampled.

| Field | Meaning |
|---|---|
| `waveforms` | waveform names written to the pulser |
| `pulse_generator_settings` | **a plain dict, not the dataclass** — see below |
| `number_of_samples`, `number_of_elements`, `elements_length_bins`, `ideal_length` | sampling geometry |
| `laser_rising_bins`, `laser_falling_bins` | laser pulse edges, read by the extraction methods |
| `step_waveform_list`, `ensemble_info` | sequence-specific |
| `_legacy_data` | catch-all for keys that are not declared fields |

Three things to know:

- **`__bool__` returns `bool(self.waveforms)`.** Logic all over the toolchain does
  `if ensemble.sampling_information:` to mean *"has this actually been sampled?"* — do not add fields
  that would change that meaning.
- **`pulse_generator_settings` is typed `Optional[dict]` on purpose**, even though it conceptually is
  a `PulseGeneratorSettings`. Two call sites depend on it being a plain dict: the sampling-cache
  comparison in `sample_pulse_sequence()` (dict equality) and
  `pulse_extraction_methods/basic_extraction_methods.py` (dict subscripting).
- **`_legacy_data` absorbs unknown keys** so nothing is ever lost round-tripping an old file. It also
  means a typo'd key is silently accepted — check `_field_names()` if something you set does not seem
  to take effect.

This is the only class in the package with a YAML representer and constructor registered for it
(in [`../sequence_generator_logic.py`](../sequence_generator_logic.py), just after the imports), so it
can be written into and read back out of `.ensemble`/`.sequence` files.

#### `SequenceGeneratorSettings` *(frozen)* — the aggregate

```python
generation_parameters    : GenerationParameters
pulse_generator_settings : PulseGeneratorSettings
```

Persisted as the single `_generator_settings` `StatusVar`. Also carries `LEGACY_STATUS_VAR_KEYS` and
`from_legacy_dict()` for reading pre-refactor status files, where `_generation_parameters` was its own
top-level key.

#### Module-level defaults

`_DEFAULT_GENERATION_PARAMETERS` and `_DEFAULT_PULSE_GENERATOR_SETTINGS` are single shared instances
used both as `from_dict()`'s per-section fallbacks and as `_default_generator_settings()`'s values.
They live here rather than in `sequence_generator_logic.py` because this is the lower-level file — the
import can only go one way.

Sharing one instance is safe precisely *because* these classes are frozen (rule 1).

---

### `pulsed_measurement_logic_data.py`

Everything `PulsedMeasurementLogic` stores: how to acquire, how to analyze, and the data itself.

#### `FastCounterSettings` *(frozen)*

| Field | Type | Meaning |
|---|---|---|
| `bin_width` | float | s per time bin |
| `record_length` | float | s recorded after each trigger |
| `number_of_gates` | int | gates per sweep; forced to 0 when the counter is ungated |
| `is_gated` | bool | never persisted — always re-read from hardware on activation |

#### `MicrowaveSettings` *(frozen)*

`power` (dBm), `frequency` (Hz), `use_ext_microwave` (bool). These describe the **external** CW
microwave source, not the pulsed MW on the AWG.

#### `ReadoutSettings` *(frozen)* — how the measurement is interpreted

| Field | Type | Meaning |
|---|---|---|
| `invoke_settings` | bool | if True, auto-fill the rest from the loaded asset's `MeasurementInformation` |
| `controlled_variable` | `np.ndarray` | the x-axis: tau values, frequencies, ... |
| `number_of_lasers` | int | laser pulses per sweep |
| `laser_ignore_list` | `list[int]` | indices to drop (e.g. reference pulses) |
| `alternating` | bool | two interleaved signals per point |
| `units` | `(str, str)` | x/y units for plots and saved files |
| `labels` | `(str, str)` | x/y axis labels |

`__post_init__` enforces two invariants on every construction path: `controlled_variable` is copied
and its `writeable` flag cleared (so a frozen settings object really is read-only, and a caller's live
array is never affected), and `laser_ignore_list` is sorted.

> **Numpy gotcha:** because a `np.ndarray` field is present, the auto-generated `__eq__` raises
> *"truth value of an array is ambiguous"* the moment two instances are compared. `ReadoutSettings`
> — and therefore `PulsedMeasurementSettings`, which contains one — has **not** been given a custom
> `__eq__`, so `settings_a == settings_b` will blow up; the round-trip tests work around it by
> spot-checking individual fields instead. `MeasurementInformation` hit the same problem and *did*
> get a custom `__eq__` built on `np.array_equal`. If you add an array field to a class anything
> compares, do the same.

#### `PulsedMeasurementData` *(mutable)* — the data itself

`raw_data`, `laser_data`, `signal_data`, `signal_alt_data`, `measurement_error` (all `np.ndarray`),
plus `elapsed_time` and `elapsed_sweeps`.

Mutated in place by the analysis loop. `copy()` deep-copies every array, so a snapshot never keeps
changing under its holder.

#### `DataStashCache` *(mutable)*

Stash/recall for raw data the user wants to keep across a re-run: `cache: dict` keyed by a user tag,
plus `active_tag`. `stash()` copies the array in; `recall()` sets the active tag and returns the entry.
Runtime only, never persisted.

#### `AlternativeSignalSettings` *(mutable)*

`method: Optional[str]` plus a free-form `parameters: dict`. The parameters are not fixed fields
because `AltPlotAnalyzer` discovers them at runtime from whichever `AltPlotMethodBase` subclasses are
loaded — including lab-supplied ones via `alt_plot_import_path` — so the valid key set is unknown at
class-definition time.

Note `update_from_dict()` here mutates and returns `self`, unlike the frozen classes which return a
new object. That is required: `AltPlotAnalyzer` holds this exact instance.

#### `ExecutionState` *(mutable)* — the pause-aware clock

`is_paused`, `start_time`, `time_of_pause`, `elapsed_pause`, with `start()`, `pause()`, `unpause()`
and `get_live_elapsed_time()`.

The trick worth knowing: `unpause()` pushes `start_time` *forward* by the pause duration, so elapsed
time never counts paused seconds and no separate accumulator is needed.

#### `MeasurementInformation` *(mutable, dict-like)*

Attached to a pulse object as `.measurement_information`, written by predefined generate methods to
describe what they built. Feeds `ReadoutSettings` when `invoke_settings` is on.

Fields: `number_of_lasers`, `controlled_variable`, `laser_ignore_list`, `alternating`,
`counting_length`, `units`, `labels` — all default to `None`, meaning "not yet known".

- `_MANDATORY_FIELDS` (a `ClassVar`, so it is *not* a dataclass field) lists the five needed to invoke
  settings; `is_valid` and `__bool__` check them. This replaced a "check 5 keys exist in a dict"
  convention scattered across call sites.
- Implements `__getitem__`/`__setitem__`/`get`/`update`/`__contains__` so predefined methods can keep
  writing `block_ensemble.measurement_information['alternating'] = False`. Unlike a raw dict, an
  unknown key raises `KeyError` listing the valid field names — typos are caught immediately.
- Defines a custom `__eq__` for the numpy reason described above.

#### `AnalysisParameters`, `ExtractionParameters` *(dict subclasses)*

Persisted settings for `PulseAnalyzer` / `PulseExtractor`: the selected method under the `'method'`
key plus that method's keyword arguments alongside it. Both expose `.method` and `.parameters`
(everything except `'method'`) as read-only properties.

Shared live objects — see convention 4.

#### `GenerationMethodParameters` *(dict subclass)*

The keyword arguments used to generate the loaded asset, e.g. `{'xy8_order': 8}`. Kept a dict because
the key set depends entirely on which predefined generate method ran, and because
`PulseBlockEnsemble`/`PulseSequence` already store it as a plain dict.

#### `MetadataDictRepresentation` *(dict subclass)*

Pure display helper. `qudi.util.datastorage`'s header writer renders each metadata value with
`repr()` and prefixes every resulting line with the comment marker. A plain dict's `repr()` never
breaks lines, so a big settings dict became one unreadable wall of text. This subclass overrides
`__repr__` to return `pprint.pformat(...)`, which does contain real newlines — so the existing
line-prefixing logic handles it with no changes to `DataStorage`.

#### `PulsedMeasurementSettings` *(frozen)* — the aggregate

```python
microwave_settings        : MicrowaveSettings
fast_counter_settings     : FastCounterSettings
readout_settings          : ReadoutSettings
alternate_signal_settings : AlternativeSignalSettings   # shared, mutated in place
extraction_parameters     : ExtractionParameters        # shared, mutated in place
analysis_parameters       : AnalysisParameters          # shared, mutated in place
```

Deliberately **not** here: `timer_interval_s`, `fit_configs` — operational bookkeeping, persisted as
their own `StatusVar`s. Also not here: `sampling_information`, `measurement_information`,
`generation_method_parameters` — those live on the loaded asset and are exposed as properties over
that one reference rather than copied, which removed a save/load race.

Carries `LEGACY_STATUS_VAR_KEYS` (the ~20 old `StatusVar` names, including the name-mangled
`_PulsedMeasurementLogic__microwave_power` style ones) and `from_legacy_dict()`.

#### Module-level defaults

`_DEFAULT_MICROWAVE_SETTINGS`, `_DEFAULT_FAST_COUNTER_SETTINGS`, `_DEFAULT_READOUT_SETTINGS` — shared
frozen instances, same rationale as in the generator file.

Note the mutable defaults are **not** shared: `_default_measurement_settings()` builds a fresh
`AlternativeSignalSettings()`/`ExtractionParameters()`/`AnalysisParameters()` on every call, so one
measurement's live state can never leak into another's defaults.

---

### `pulsed_measurement.py`

The top-level snapshot. Read the containment tree above alongside this section.

#### `Settings` *(frozen)*

```python
measurement_settings : PulsedMeasurementSettings
generator_settings   : Optional[SequenceGeneratorSettings] = None
```

Bundles the two independently-persisted settings containers. Each stays owned by its own logic
module's `StatusVar`; this only holds references. `generator_settings` is `Optional` because
`PulsedMeasurementLogic` has no `Connector` to `SequenceGeneratorLogic` and cannot fetch it alone —
it is `None` whenever the snapshot was built by code with only measurement-logic access.

#### `PulsedData` *(mutable)*

`measurement_data: PulsedMeasurementData` plus `fit_result` / `fit_result_alt`.

The fit results are `lmfit.model.ModelResult` objects with a **one-way** export path:
`FitContainer.dict_result()` gives model name and parameter values/stderr for the saved metadata, and
there is no reconstruction path — lmfit does not offer a simple one. So `from_dict()` deliberately
does not restore them, and a `to_dict()`/`from_dict()` round trip loses them. This is expected, and
the round-trip test asserts it.

#### `PulseObjects` *(mutable)*

`sequence` (the loaded top-level asset), plus `ensembles` and `blocks` — independent copies of
everything `sequence` references **by name**. The real objects live in `SequenceGeneratorLogic`'s
saved-asset registries and are untouched; these copies are resolved via
`SequenceGeneratorLogic.resolve_asset_closure()`.

The point is that a saved measurement's dict shows every block/ensemble definition in full rather than
just a name, and stays frozen in time — editing a same-named asset later never changes an
already-taken snapshot.

- `elements` is a **property**, not a field: every `PulseBlockElement` across every block, flattened.
  It is not exported by `to_dict()` because each element already appears inside its block's
  `element_list`; exporting it would duplicate all of them.
- `to_metadata_dict()` is a trimmed, display-only variant for saved-file headers. It drops per-sample
  arrays (`laser_rising_bins`, `elements_length_bins`, `_legacy_data`) and duplicated sections so the
  header shows sequence *structure* rather than thousands of numbers. There is no
  `from_metadata_dict()`.

#### `PulsedMeasurement` *(mutable)* — the root

```python
settings : Settings
data     : Optional[PulsedData] = None
objects  : PulseObjects = field(default_factory=PulseObjects)
```

(`field(default_factory=...)` rather than `= PulseObjects()`: a dataclass refuses a mutable default
outright, because one shared instance would be handed to every object ever constructed. The same
applies to every `dict`/`list`/`set`/`np.ndarray` default in this package.)

Built by `PulsedMeasurementLogic.get_pulsed_measurement()` / `PulsedMasterLogic.get_pulsed_measurement()`
as a frozen-in-time snapshot — `data` and `objects` are populated from `.copy()` calls, not live
references.

It doubles as `PulsedMeasurementLogic`'s **actual live storage**: the module's `_settings`,
`measurement_data`, `_loaded_asset`, `_fit_result` and `_fit_result_alt` are all properties reading
through one `_pulsed_measurement` instance rather than independent attributes.

---

### `pulsed_master_logic_data.py`

#### `PulsedMasterStatus` *(dict subclass)*

Ten busy/running flags, all defaulting to `False`: `sampling_ensemble_busy`, `sampling_sequence_busy`,
`sampload_busy`, `loading_busy`, `pulser_running`, `measurement_running`, `microwave_running`,
`predefined_generation_busy`, `fitting_busy`, `benchmark_busy`.

Each has a property getter/setter over the dict entry, so `PulsedMasterLogic` gets typo-proof named
access while `pulsed_maingui.py`'s existing `status_dict['flag']` reads and writes keep working.

#### `FitContainers` *(frozen)*

`primary` and `alternative` `FitContainer` instances, replacing an anonymous `(fc, alt_fc)` tuple.
Implements `__iter__`/`__len__`/`__getitem__` so `fit_containers[0]` still works.

---

### `settings_coercion.py`

Not dataclasses, but the shared front door every settings setter goes through.

Every setter has the signature `def set_x_settings(self, settings=None, **kwargs)` and must accept
four calling styles, all legal for backward compatibility:

```python
logic.set_generation_parameters(rabi_period=50e-9)                          # kwargs
logic.set_generation_parameters({'rabi_period': 50e-9})                     # dict
logic.set_generation_parameters(generation_parameters_instance)             # dataclass
logic.set_generation_parameters({'rabi_period': 50e-9}, laser_length=2e-6)  # both
```

#### `coerce_settings(value, kwargs, current, cls)` → an instance of `cls`

| `value` is... | result |
|---|---|
| an instance of `cls` | **full replacement** — `current` is ignored (`replace(value, **kwargs)` if kwargs given) |
| a dict | **partial patch** — `current.update_from_dict({**value, **kwargs})` |
| `None` | `current.update_from_dict(kwargs)`; with no kwargs at all this is a harmless refresh |
| anything else | `SettingsTypeError` |

Three details that are not obvious:

- **The `cls` check comes before the dict check** because of convention 5 — for a dict-subclass
  settings object both are true, and dict-first would misread a full replacement as a patch.
- **`kwargs` is a plain positional argument, not `**kwargs`.** If it were `**kwargs`, a setting
  literally named `value`, `current` or `cls` would collide with the helper's own parameter names.
- **Passing a bare `BaseGenerationParameters` subclass is rejected**, because a contributor is not an
  instance of the merged `GenerationParameters`. That is intentional: a contributor describes a
  *fragment* of the schema, and treating it as a full replacement would reset every other parameter to
  its default.

#### `as_settings_dict(value, kwargs, cls=None)` → a plain dict

The relay-layer counterpart. `PulsedMasterLogic` forwards settings to the owning logic module across a
queued cross-thread connection, and a `QtCore.Signal(dict)` cannot carry a dataclass — so it must
flatten first. It has no `current` parameter because a relay has nothing to patch against; it just
forwards whatever partial information it was handed.

#### `SettingsTypeError(TypeError)` — the two-tier error design

| Exception | Means | Handling |
|---|---|---|
| `SettingsTypeError` | bad **input** from a user or script | caught, logged, settings left untouched |
| plain `TypeError` | a **bug in the calling module** (wrong `cls`, unknown kwarg name) | not caught — propagates loudly |

Because `SettingsTypeError` inherits from `TypeError`, `except SettingsTypeError` catches only the
recoverable kind.

The queued `@QtCore.Slot(dict)` setters **log instead of raising**: an exception inside a queued slot
has no caller to propagate to, so at best Qt swallows it and at worst it takes the application down
mid-measurement. They log the error, re-emit the unchanged settings so the GUI snaps back, and return.
The plain property setters *do* raise, because those are called directly from Python where a traceback
is useful.

---

## Where instances come from

The docstrings explain what each class *is* better than they explain who *builds* it. There are four
routes.

### Route 1 — defaults at startup

```
_default_measurement_settings()   in ../pulsed_measurement_logic.py
_default_generator_settings()     in ../sequence_generator_logic.py
```

Both are factory functions reusing the module-level `_DEFAULT_*` frozen instances, plus fresh mutable
ones. They serve as the `StatusVar` default *and* as the fallback when a stored value turns out to be
malformed.

### Route 2 — restored from the status file at activation

Three `StatusVar` declarations do all persistence. qudi-core calls the `representer` on shutdown and
the `constructor` on activation.

| StatusVar | Module | On-disk key | Round trip |
|---|---|---|---|
| `_pulsed_measurement` | `PulsedMeasurementLogic` | `_settings` | representer writes only `settings.measurement_settings.to_dict()`; `data`/`objects` are transient and rebuilt each activation |
| `_generator_settings` | `SequenceGeneratorLogic` | `_generator_settings` | `SequenceGeneratorSettings.to_dict()` / `.from_dict()` |
| `pulser_benchmarks` | `SequenceGeneratorLogic` | `pulser_benchmarks` | `PulserBenchmarks.to_dict()` / `.from_dict()` |

Plus the independent ones that are deliberately outside the settings objects: `timer_interval_s` and
`fit_configs` on `PulsedMeasurementLogic`.

**Legacy migration** runs once, before the normal path: `_migrate_legacy_settings_if_needed()` in each
logic module reads the raw status file, checks it against that class's `LEGACY_STATUS_VAR_KEYS`, and
if it matches the old format, backs the file up and rebuilds via `from_legacy_dict()`.

### Route 3 — a live edit from the GUI or a script

```
you change a widget on the Predefined Methods tab
  └─► pulsed_maingui.generation_parameters_changed()      collects widgets into a plain dict
      └─► PulsedMasterLogic.set_generation_parameters(dict)
          └─► as_settings_dict(...)                      normalize to a DICT (a Signal can carry it)
              └─► sigSamplingSettingsChanged.emit(dict)   [queued — thread hop]
                  └─► SequenceGeneratorLogic.set_generation_parameters(dict)
                      └─► coerce_settings(...)            normalize to a DATACLASS
                          └─► current.update_from_dict()  per-field, keeps unmentioned fields
                              └─► replace(self._generator_settings, generation_parameters=new)
                                  └─► sigSamplingSettingsUpdated.emit(...)   GUI redraws
```

Two normalizations, one per layer: the relay needs a dict because that is all a signal can carry, the
owner needs a dataclass because that is what it stores.

The same shape applies to `set_fast_counter_settings`, `set_ext_microwave_settings`,
`set_measurement_settings` and `set_pulse_generator_settings`.

### Route 4 — attached to pulse objects

`SamplingInformation()`, `MeasurementInformation()` and `GenerationMethodParameters()` are constructed
**empty** in every `PulseBlock` / `PulseBlockEnsemble` / `PulseSequence` `__init__` and
`*_from_dict()` in [`../pulse_objects.py`](../pulse_objects.py) — around fifteen sites. They are then
filled in by `SequenceGeneratorLogic`:

- `generate_predefined_sequence()` sets `generation_method_parameters` from the method's kwargs;
- the predefined generate method itself writes `measurement_information`;
- `sample_pulse_block_ensemble()` / `sample_pulse_sequence()` write `sampling_information`;
- `clear_pulser()` and the various invalidation paths reset them to fresh empty instances.

---

## Neighbour map

Classes referenced from here that live elsewhere. Named, not documented — see their own files. The
one exception is `is_sequence` below, which is documented here because *why* it is not a field of any
class in this package is the interesting part.

| Class | Lives in | Relationship |
|---|---|---|
| `PulseBlock`, `PulseBlockEnsemble`, `PulseSequence`, `PulseBlockElement` | [`../pulse_objects.py`](../pulse_objects.py) | held by `PulseObjects`; carry `SamplingInformation`/`MeasurementInformation`/`GenerationMethodParameters`. The two loadable ones expose `is_sequence` — see below |
| `PulseEnvelope`, `PulseEnvelopeType` | [`../sampling_functions.py`](../sampling_functions.py) | the type of `CoreGenerationParameters.pulse_envelope` |
| `BenchmarkTool` | `qudi.util.benchmark` | the two fields of `PulserBenchmarks` |
| `FitContainer` | `qudi.util.datafitting` | the two fields of `FitContainers`; `dict_result()` is the one-way fit export |
| `dataclass_representer`, `sampling_information_constructor` | `qudi.util.yaml_helpers` | YAML round trip for `SamplingInformation`, registered in `../sequence_generator_logic.py` |

#### `is_sequence` — telling the two loadable assets apart

`PulseBlockEnsemble.is_sequence` is `False`, `PulseSequence.is_sequence` is `True`. Use it when you
hold an asset object whose kind you do not know:

```python
if asset.is_sequence:
    ...
```

**It is a class attribute, not an instance attribute, and that is the whole point.** A class attribute
is not part of pickled instance state, so every `.ensemble`/`.sequence` already saved to disk gained
it the moment the classes were updated — no migration, and it cannot drift out of sync with the
object's actual type. Had it been a persisted field instead, convention 3's missing-key tolerance
would have defaulted it to `False` on every pre-existing file, silently reclassifying every saved
`PulseSequence` as an ensemble.

It is therefore **not** in `to_dict()`, `to_metadata_dict()` or any pickle. The saved form carries the
distinction as a class-name string instead — `PulseObjects.to_dict()['sequence']['type']`, which is
what `from_dict()` branches on, and `'loaded asset type'` in each `.dat` header. Keep those as strings:
they must survive without the classes present, and a name still works if a third asset type appears.

Three places it deliberately does **not** replace `isinstance`:

| Pattern | Why |
|---|---|
| `if not isinstance(x, PulseBlockEnsemble): error` | asks *"is this the right type at all"* — `is_sequence == False` doesn't rule out `None`, a `str` or a dict |
| `if isinstance(x, PulseSequence): x = x.name` | object-or-name coercion; the other branch is a `str`, which has no `is_sequence` |
| anywhere only a name or `LoadedAsset.asset_type` is available | there is no object to ask — `asset_type` comes from the pulser hardware |

Where the object *is* in hand but might not be a pulse asset at all, use the tri-state form so an
unrelated object still reaches your error branch instead of raising `AttributeError` (see
`_resolve_asset_closure` in [`../sequence_generator_logic.py`](../sequence_generator_logic.py)):

```python
asset_is_sequence = getattr(asset, 'is_sequence', None)   # None -> not a pulse asset
```

> **Not to be confused with** `sigPredefinedSequenceGenerated`'s `produced_sequence` payload, which
> asks whether a *generate method returned* any sequences — a different question about a different
> subject.

### The layering rule

`pulsed_data/` sits **below** `pulse_objects.py`. `pulse_objects.py` imports from here; nothing here
may import from it at module level. Where a type hint needs one, `pulsed_measurement.py` uses
`if TYPE_CHECKING:` and quoted annotations, and `PulseObjects.from_dict()` imports inside the
function body.

The same rule applies between this folder and the logic modules: `sequence_generator_logic_data.py`
cannot import from `sequence_generator_logic.py`, which is why the shared `_DEFAULT_*` literals are
defined down here.

And within this folder, `generation_parameter_extensions.py` must never import from
`sequence_generator_logic_data.py` — a cycle there would make the contributor merge silently skip
extensions depending on import order.

---

## Extending this — notes for physicists

### Adding a global generation parameter (the common case)

Edit **only** [`generation_parameter_extensions.py`](generation_parameter_extensions.py). Add one
frozen dataclass inheriting `BaseGenerationParameters`:

```python
@dataclass(frozen=True)
class NVCentreParameters(BaseGenerationParameters):
    """Extra generation parameters for the NV setup."""

    green_aom_delay: float = 700e-9
    readout_channel: str = 'd_ch4'
```

That is the whole change — there is no restore method to write, because the declared types already
say how each value is converted on the way back in (see the
[`_coerce_fields` table above](#basegenerationparameters--the-marker-base-class)). A widget appears on
the Predefined Methods tab, the value persists across restarts, and predefined methods read it as:

```python
self.generation_parameters['green_aom_delay']                        # dict style
self.generator_settings.generation_parameters.green_aom_delay        # attribute style
```

**Rules, each with a real consequence if broken:**

| Rule | What happens otherwise |
|---|---|
| Decorate the class `@dataclass(frozen=True)` | `TypeError` at import naming the class. Without the decorator its annotations never become fields, so it would contribute nothing and its parameters would simply not exist |
| Every field needs a default | `TypeError` at import — a merged dataclass cannot have a no-default field after a defaulted one |
| Field names must be unique across all contributors | `TypeError` at import naming both classes. This is a *feature* — silent last-wins would change a measurement parameter with no warning |
| Types must be `str`/`int`/`float`/`bool`/`Enum`/`PulseEnvelope` | a warning is logged at import and no widget is built for it (see `_create_pm_global_params` in [`../../../gui/pulsed/pulsed_maingui.py`](../../../gui/pulsed/pulsed_maingui.py)); the parameter still works from scripts |
| Declare the class at import time, not from a config path | the merge runs at module import, long before qudi-core restores status variables |
| Never pass a bare contributor to `set_generation_parameters()` | rejected by `coerce_settings` — see the note in the `settings_coercion.py` section |

Suffix conventions the GUI picks up automatically: a float whose name contains `amp` or `volt` gets a
`V` suffix, `freq` gets `Hz`, and `tau`/`period`/`time`/`delay`/`laser_length` get `s`.

Also note: any generation parameter whose name **ends in `_channel`** is treated as a channel
specifier by `set_pulse_generator_settings()` and will be auto-corrected if it names a channel that is
not in the active activation config.

### Adding a field to any other settings dataclass

Six steps, in order. Skipping any of the middle ones fails silently rather than loudly.

1. **Declare the field** with a type and a default.
2. **`to_dict()`** — add the key. Copy mutable values (`dict(...)`, `list(...)`, `.copy()`).
3. **`from_dict()`** — add the key *tolerantly*: `data.get('name', default)`, never `data['name']`.
4. **`update_from_dict()`** — add the key with `data.get('name', self.name)`.
5. **`_DEFAULT_*`** — update the module-level literal if the class has one.
6. **Widget** — if it is user-facing, add it in `pulsed_maingui.py`.

If the field is a `np.ndarray`, also check whether anything compares two instances of the class; if so
you need a custom `__eq__` (see `MeasurementInformation.__eq__`).

If the class is one of the *shared mutable* ones, remember its `update_from_dict()` must mutate and
return `self`, not build a new object.

### Why your old status file still works — and what to do when it does not

Loading is tolerant at three levels: unknown keys are ignored, missing keys fall back to defaults, and
whole missing sections fall back to the shared `_DEFAULT_*` objects. `SamplingInformation` goes
further and keeps unknown keys in `_legacy_data`.

If settings do reset unexpectedly:

1. Find the status file — `qudi/`'s app data dir, one file per logic module.
2. Check whether it is the pre-refactor format: if its top-level keys match a class's
   `LEGACY_STATUS_VAR_KEYS`, the migration path should have converted it and left the original
   alongside it as `<status file>.legacy_backup`.
3. If a `from_dict` raised, the `StatusVar` constructor swallowed it and returned the default object —
   check the qudi log for the traceback rather than assuming the file was empty.

### Pitfalls worth knowing before you debug something

- **`replace()` returns a new object.** `replace(settings, power=-20)` on its own does nothing; you
  must assign the result. Frozen classes will not let you assign the field directly.
- **Numpy fields break the generated `__eq__`.** Symptom: *"The truth value of an array with more than
  one element is ambiguous."*
- **`if sampling_information:` means "has been sampled"**, not "is not None" — `__bool__` checks
  `waveforms`. Same for `LoadedAsset` (needs both name and type) and `MeasurementInformation` (needs
  all five mandatory fields).
- **Shared mutable objects must not be rebound.** If you replace `analysis_parameters` with a new
  object instead of `.update(...)`-ing it, `PulseAnalyzer` keeps using the old one and your change
  appears to be ignored.
- **`GenerationParameters` is pickled into every saved `.ensemble`/`.sequence`.** Renaming the
  module-level name, or building the class somewhere pickle cannot resolve, breaks every existing
  saved asset.
- **`.copy()` is not uniform.** `PulsedMeasurementData.copy()` deep-copies every array;
  `SamplingInformation.copy()` copies each mutable field individually; `MeasurementInformation.copy()`
  copies the list and the array. Check the method before assuming a snapshot is independent.
- **`_legacy_data` silently absorbs typos.** A key you set on a `SamplingInformation` that is not a
  declared field lands there and is never read by the code you expected to read it.

### Tests

| File | Pins |
|---|---|
| [`../../../../../tests/test_pulsed_measurement.py`](../../../../../tests/test_pulsed_measurement.py) | the bulk of it — `to_dict()`/`from_dict()` round trips for `PulsedMeasurement`/`PulsedMeasurementSettings`/`SequenceGeneratorSettings`/`SamplingInformation`; tolerance of a dict missing a key; that fit results are deliberately *not* restored; both `from_legacy_dict()` paths against realistic pre-refactor status dicts; that `LEGACY_STATUS_VAR_KEYS` is not a dataclass field; `.copy()` independence for pulse objects and `PulsedMeasurementData`; pickle round trip of a whole snapshot; block/ensemble closure resolution |
| [`../../../../../tests/test_pulsed_measurement_logic_data.py`](../../../../../tests/test_pulsed_measurement_logic_data.py) | `ExecutionState` pause behaviour, `DataStashCache` stash/recall, `AnalysisParameters`/`ExtractionParameters` round trip |

Run them with:

```
python -m pytest tests/test_pulsed_measurement.py tests/test_pulsed_measurement_logic_data.py
```

> `tests/test_migration.py` is **not** part of this suite despite the name. It is a scratch script
> with a hardcoded absolute path to one person's `rabi.ensemble`, containing no test function and
> doing its work at import time — pytest will error collecting it on any other machine. If you want a
> real regression test for unpickling a legacy `.ensemble`, it needs rewriting around a fixture file.

If you add a field, add it to the relevant round-trip test — that is what catches a `to_dict()` and
`from_dict()` that have drifted apart.
