import unittest
from unittest.mock import MagicMock

from _enigma_stubs import bootstrap
bootstrap()

from FBCChannelSpeedChange.resource_arbiter import (
    ResourceArbiter, STATE_PREPARED, STATE_ENDED,
)
from FBCChannelSpeedChange.config import cfg


class FakeTimer:
    def __init__(self, state):
        self.state = state


class FakeRecordTimer:
    def __init__(self):
        self.on_state_change = []
        self.timer_list = []


class FakeSession:
    def __init__(self, pipshown=False):
        self.pipshown = pipshown


class ArbiterTests(unittest.TestCase):

    def setUp(self):
        # Force config defaults explicitly (the test process state is shared).
        cfg.release_for_recording.value = True
        cfg.release_for_pip.value = True

    def test_recording_prepared_calls_release(self):
        pool = MagicMock()
        rt = FakeRecordTimer()
        arbiter = ResourceArbiter(
            pool,
            record_timer_provider=lambda: rt,
            session_provider=lambda: FakeSession(),
        )
        # Hook the on_state_change callbacks directly without involving PiP/timers.
        rt.on_state_change.append(arbiter._on_record_state_change)
        timer = FakeTimer(state=STATE_PREPARED)
        for cb in rt.on_state_change:
            cb(timer)
        pool.release_for.assert_called_once()
        self.assertTrue(arbiter.priority_active())

    def test_recording_ended_decrements_counter(self):
        pool = MagicMock()
        rt = FakeRecordTimer()
        arbiter = ResourceArbiter(
            pool,
            record_timer_provider=lambda: rt,
            session_provider=lambda: FakeSession(),
        )
        arbiter._on_record_state_change(FakeTimer(state=STATE_PREPARED))
        self.assertEqual(arbiter._record_active_count, 1)
        arbiter._on_record_state_change(FakeTimer(state=STATE_ENDED))
        self.assertEqual(arbiter._record_active_count, 0)
        self.assertFalse(arbiter.priority_active())

    def test_release_skipped_when_config_disabled(self):
        pool = MagicMock()
        rt = FakeRecordTimer()
        arbiter = ResourceArbiter(
            pool,
            record_timer_provider=lambda: rt,
            session_provider=lambda: FakeSession(),
        )
        cfg.release_for_recording.value = False
        try:
            arbiter._on_record_state_change(FakeTimer(state=STATE_PREPARED))
            pool.release_for.assert_not_called()
        finally:
            cfg.release_for_recording.value = True

    def test_pip_poll_releases_on_transition(self):
        pool = MagicMock()
        session = FakeSession(pipshown=False)
        arbiter = ResourceArbiter(
            pool,
            record_timer_provider=lambda: None,
            session_provider=lambda: session,
        )
        arbiter._poll_pip()  # not shown -> nothing
        pool.release_for.assert_not_called()
        session.pipshown = True
        arbiter._poll_pip()
        pool.release_for.assert_called_once()
        # Repeated poll while still active must not double-release.
        arbiter._poll_pip()
        self.assertEqual(pool.release_for.call_count, 1)
        # Pip stops -> no extra release, just flag flip.
        session.pipshown = False
        arbiter._poll_pip()
        self.assertEqual(pool.release_for.call_count, 1)
        self.assertFalse(arbiter._pip_active)


if __name__ == "__main__":
    unittest.main()
