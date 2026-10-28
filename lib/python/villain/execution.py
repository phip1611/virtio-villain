# SPDX-License-Identifier: Apache-2.0
"""Bounded work submission with caller owned execution and result policies."""

from concurrent.futures import FIRST_COMPLETED, wait


class CompletionQueue:
    """Yield work items and completed futures without retaining consumed results."""

    def __init__(self, pool, function, items, *, limit, stop=None):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("The pending work limit must be a positive integer")
        self.pool = pool
        self.function = function
        self.items = iter(items)
        self.limit = limit
        self.stop = stop
        self.pending = {}
        self.exhausted = False

    def __iter__(self):
        return self

    def __next__(self):
        while (not self.exhausted and len(self.pending) < self.limit
               and (self.stop is None or not self.stop.is_set())):
            try:
                item = next(self.items)
            except StopIteration:
                self.exhausted = True
                break
            self.pending[self.pool.submit(self.function, item)] = item
        if not self.pending:
            raise StopIteration
        done, _ = wait(self.pending, return_when=FIRST_COMPLETED)
        future = next(iter(done))
        return self.pending.pop(future), future
