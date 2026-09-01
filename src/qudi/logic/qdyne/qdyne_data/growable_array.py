# -*- coding: utf-8 -*-
"""An append-only numpy buffer that grows in amortised constant time.

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
import numpy as np

__all__ = ['GrowableArray']


class GrowableArray:
    """A 1-D numpy buffer you can append to without copying everything each time.

    Why this exists: the measurement loop used to accumulate with

        self.data.raw_data = np.append(self.data.raw_data, new_chunk)

    `np.append` allocates a fresh array and copies the whole history on every call, so accumulating
    N samples in chunks costs O(N**2) of copying. A Qdyne run gathers millions of time tags over
    hours, and that quadratic term ends up dominating the entire measurement.

    Here the buffer keeps spare capacity and doubles when it runs out, so appending is amortised
    O(1) per sample - the same trick a Python list uses. `view` hands back `buffer[:size]`, which is
    a numpy *view* rather than a copy, so readers pay nothing and downstream numpy stays fast on
    contiguous memory.
    """

    #: Capacity for the first allocation. Small enough not to waste memory on a short run, large
    #: enough that a typical poll does not trigger several doublings immediately.
    INITIAL_CAPACITY = 1024

    def __init__(self, dtype=np.int64, initial_capacity: int = INITIAL_CAPACITY):
        self._dtype = np.dtype(dtype)
        self._buffer = np.empty(max(1, int(initial_capacity)), dtype=self._dtype)
        self._size = 0

    # ------------------------------------------------------------------ reading

    @property
    def view(self) -> np.ndarray:
        """The filled part of the buffer, as a view.

        No copy is made, so this is cheap to call in a loop - but it is also a live window onto the
        buffer. Take `.copy()` if you need something that survives the next append, since a growth
        reallocates and leaves the old view pointing at a stale array.
        """
        return self._buffer[:self._size]

    @property
    def dtype(self) -> np.dtype:
        return self._dtype

    @property
    def capacity(self) -> int:
        return self._buffer.size

    def __len__(self) -> int:
        return self._size

    def __array__(self, dtype=None, copy=None):
        """Let numpy treat this as an array, so np.asarray()/np.mean() and friends just work."""
        data = self.view
        if dtype is not None:
            return data.astype(dtype, copy=bool(copy) if copy is not None else True)
        return data.copy() if copy else data

    # ------------------------------------------------------------------ writing

    def _ensure_capacity(self, required: int) -> None:
        if required <= self._buffer.size:
            return
        new_capacity = max(1, self._buffer.size)
        while new_capacity < required:
            new_capacity *= 2
        grown = np.empty(new_capacity, dtype=self._dtype)
        grown[:self._size] = self._buffer[:self._size]
        self._buffer = grown

    def append(self, chunk) -> None:
        """Append a chunk of samples. Amortised O(len(chunk))."""
        chunk = np.asarray(chunk, dtype=self._dtype).ravel()
        if chunk.size == 0:
            return
        self._ensure_capacity(self._size + chunk.size)
        self._buffer[self._size:self._size + chunk.size] = chunk
        self._size += chunk.size

    def replace(self, values) -> None:
        """Discard everything and hold `values` instead - used when loading a saved measurement."""
        values = np.asarray(values, dtype=self._dtype).ravel()
        self._buffer = values.copy() if values.size else np.empty(
            self.INITIAL_CAPACITY, dtype=self._dtype
        )
        self._size = values.size

    def clear(self) -> None:
        """Drop the contents but keep the allocation, so a restarted measurement does not re-grow
        from scratch."""
        self._size = 0
