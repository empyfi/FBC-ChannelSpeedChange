"""Tests for the controller's re-arm planning helper.

Covers the HISTORY-on-convergence collapse: during linear bouquet
walking the HISTORY target ends up on the same service as PREV
(walking Channel up) or NEXT (walking Channel down). The helper drops
HISTORY in that case so the pool does not hold a redundant recordable
on the same service.
"""

import unittest

from _enigma_stubs import bootstrap
bootstrap()

from FBCChannelSpeedChange.controller import _collapse_history_on_convergence


class FakeRef:
    def __init__(self, s):
        self._s = s
        try:
            self.type = int(s.split(":", 1)[0])
        except (ValueError, IndexError):
            self.type = 1

    def toString(self):
        return self._s


# Service references shaped like enigma2's serviceref. The last colon
# segment is the channel name and is volatile (users rename channels);
# _ref_key drops it before comparison.
REF_A = FakeRef("1:0:1:A:0:0:0:0:0:0:Channel A")
REF_A_RENAMED = FakeRef("1:0:1:A:0:0:0:0:0:0:Channel A renamed")
REF_B = FakeRef("1:0:1:B:0:0:0:0:0:0:Channel B")
REF_C = FakeRef("1:0:1:C:0:0:0:0:0:0:Channel C")


class CollapseHistoryTests(unittest.TestCase):

    def test_no_convergence_leaves_all_three_armed(self):
        n, p, h = _collapse_history_on_convergence([REF_A], [REF_B], [REF_C])
        self.assertEqual(n, [REF_A])
        self.assertEqual(p, [REF_B])
        self.assertEqual(h, [REF_C])

    def test_history_converges_with_prev_walking_up(self):
        # Linear Channel up walk: live=bouquet[N+1], so
        # PREV=bouquet[N] (just-departed) and HISTORY=bouquet[N].
        n, p, h = _collapse_history_on_convergence([REF_C], [REF_A], [REF_A])
        self.assertEqual(n, [REF_C])
        self.assertEqual(p, [REF_A])
        self.assertEqual(h, [])

    def test_history_converges_with_next_walking_down(self):
        # Linear Channel down walk: live=bouquet[N-1], so
        # NEXT=bouquet[N] (just-departed) and HISTORY=bouquet[N].
        n, p, h = _collapse_history_on_convergence([REF_A], [REF_C], [REF_A])
        self.assertEqual(n, [REF_A])
        self.assertEqual(p, [REF_C])
        self.assertEqual(h, [])

    def test_history_only_armed_still_works(self):
        # HISTORY enabled by itself with NEXT/PREV disabled: nothing
        # to converge with, HISTORY stays armed.
        n, p, h = _collapse_history_on_convergence([], [], [REF_A])
        self.assertEqual(n, [])
        self.assertEqual(p, [])
        self.assertEqual(h, [REF_A])

    def test_empty_history_passes_through(self):
        # HISTORY disabled (or no history available): no convergence
        # check is triggered.
        n, p, h = _collapse_history_on_convergence([REF_A], [REF_B], [])
        self.assertEqual(n, [REF_A])
        self.assertEqual(p, [REF_B])
        self.assertEqual(h, [])

    def test_convergence_matches_after_channel_rename(self):
        # _ref_key drops the trailing channel name from the serviceref
        # string, so a renamed history target still compares equal to
        # the matching neighbour slot.
        n, p, h = _collapse_history_on_convergence(
            [REF_C], [REF_A], [REF_A_RENAMED])
        self.assertEqual(n, [REF_C])
        self.assertEqual(p, [REF_A])
        self.assertEqual(h, [])

    def test_history_collides_with_both_next_and_prev_dropped_once(self):
        # Degenerate corner: tiny bouquet where NEXT, PREV and HISTORY
        # all point at the same service (e.g. two-entry bouquet).
        # HISTORY is dropped; NEXT and PREV both stay (they are armed
        # against their own directions and channel-share at the pool).
        n, p, h = _collapse_history_on_convergence([REF_A], [REF_A], [REF_A])
        self.assertEqual(n, [REF_A])
        self.assertEqual(p, [REF_A])
        self.assertEqual(h, [])


if __name__ == "__main__":
    unittest.main()
