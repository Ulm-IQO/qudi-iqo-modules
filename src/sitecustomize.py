# src/sitecustomize.py
"""
Environment-wide tweak so qudi-iqo-modules comes over qudi-core
whenever modules under `qudi` are imported.
"""

def _reorder_qudi_namespace():
    try:
        import qudi
    except ImportError:
        # qudi not installed/visible in this environment
        return

    paths = list(qudi.__path__)

    # Paths coming from qudi-iqo-modules repo
    iqo_paths = [p for p in paths if "qudi-iqo-modules" in p]
    other_paths = [p for p in paths if "qudi-iqo-modules" not in p]

    if iqo_paths:
        qudi.__path__ = iqo_paths + other_paths

_reorder_qudi_namespace()
