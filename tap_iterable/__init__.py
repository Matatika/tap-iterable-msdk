"""Tap for Iterable."""

from collections import deque

from typing_extensions import Self, override


class BufferDeque(deque):
    """Specialized deque with context manager support for conditional flushing."""

    _flush = False

    @property
    def flush(self) -> bool:
        """Indicates whether the buffer is marked for flushing."""
        return self._flush

    def finalize(self):
        """Manually set the buffer to flush on exit."""
        self._flush = True

    def __enter__(self) -> Self:
        """Enter the runtime context.

        Checks if the buffer is full and sets the flush flag accordingly.
        """
        self._flush |= len(self) == self.maxlen
        return self

    def __exit__(self, *args) -> None:
        """Exit the runtime context.

        Flushes the buffer if it was full upon entering the context or if `finalize()`
        was called.
        """
        if self._flush:
            self.clear()

        self._flush = False

    @override
    def __repr__(self) -> str:
        size = len(self)

        if not size:
            descriptor = "empty"
        elif size == self.maxlen:
            descriptor = "full"
        else:
            descriptor = "active"
        return f"<buffer {descriptor} [{size}/{self.maxlen}]>"
