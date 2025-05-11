# Matisse laser for scanning

The matisse laser is intended to have its own computer with Qudi running on it. It has locally [a minimal hardware](#qudi.hardware.laser.matisse.MatisseCommander) running to talk to the laser, and we connect to it on the main experiment computer where the [main scanner](#qudi.hardware.interfuse.remote_matisse_scanner.RemoteMatisseScanner) runs.

```{contents} Table of Contents
:depth: 3
``` 

## Matisse proxy

```{autodoc2-object} qudi.hardware.laser.matisse.MatisseCommander
render_plugin = "myst"
```

## Remote matisse scanner

For now this is intended to use the [matisse proxy](#qudi.hardware.laser.matisse.MatisseCommander) on a remote computer, but could be modified to use it on the same computer if needed.

This hardware uses a [state machine](../state-machines) that is depicted below. The link are clickable and lead to the specific method documentation for the state.

```{statemachine} qudi.hardware.interfuse.remote_matisse_scanner.RemoteMatisseScanner
```

```{autodoc2-object} qudi.hardware.interfuse.remote_matisse_scanner.RemoteMatisseScanner
render_plugin = "myst"
```
