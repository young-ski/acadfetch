from __future__ import annotations

import sys
import threading

from acadfetch import naming


def test_unique_hands_out_distinct_names_under_contention():
    """The check-then-add needs to be one step when threads share the set.

    Probabilistic by nature, so the switch interval is turned down and the
    barrier repeated: without a lock this loses a name almost every round.
    """
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for _ in range(200):
            taken: set[str] = set()
            barrier = threading.Barrier(8)
            results: list[str] = []

            def worker(barrier=barrier, results=results, taken=taken) -> None:  # noqa: B008
                barrier.wait()
                results.append(naming.unique("a.bin", taken))

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert len(set(results)) == 8, results
            assert len(taken) == 8
    finally:
        sys.setswitchinterval(old)
