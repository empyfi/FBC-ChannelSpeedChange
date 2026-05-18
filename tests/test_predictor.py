import unittest

from _enigma_stubs import bootstrap
bootstrap()

from FBCChannelSpeedChange.predictor import Predictor


class FakeRef:
    def __init__(self, ref_str, flags=0):
        self._s = ref_str
        self._flags = flags

    def toString(self):
        return self._s

    def getFlags(self):
        return self._flags

    def valid(self):
        return True


class FakeLister:
    def __init__(self, refs):
        self._refs = list(refs)
        self._i = 0

    def getNext(self):
        if self._i >= len(self._refs):
            return None
        r = self._refs[self._i]
        self._i += 1
        return r


class FakeServiceCenter:
    def __init__(self, refs):
        self._refs = refs

    def list(self, _bouquet_ref):
        return FakeLister(self._refs)


def make_predictor(services, history=None, current_idx=0):
    refs = [FakeRef(s) for s in services]
    live = refs[current_idx] if current_idx is not None and refs else None
    return Predictor(
        bouquet_provider=lambda: "fake-bouquet-ref",
        service_center_provider=lambda: FakeServiceCenter(refs),
        history_provider=lambda: history or [],
        current_service_provider=lambda: live,
    ), refs


class PredictorTests(unittest.TestCase):

    def test_next_returns_immediate_neighbor(self):
        p, refs = make_predictor(["1:0:1:A:0:0:0:0:0:0:A",
                                   "1:0:1:B:0:0:0:0:0:0:B",
                                   "1:0:1:C:0:0:0:0:0:0:C"], current_idx=0)
        result = p.next_service(count=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].toString(), "1:0:1:B:0:0:0:0:0:0:B")

    def test_prev_wraps_around(self):
        p, refs = make_predictor(["1:0:1:A:0:0:0:0:0:0:A",
                                   "1:0:1:B:0:0:0:0:0:0:B",
                                   "1:0:1:C:0:0:0:0:0:0:C"], current_idx=0)
        result = p.prev_service(count=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].toString(), "1:0:1:C:0:0:0:0:0:0:C")

    def test_next_count_n(self):
        p, refs = make_predictor(["a:0:1:1:0:0:0:0:0:0:A",
                                   "a:0:1:2:0:0:0:0:0:0:B",
                                   "a:0:1:3:0:0:0:0:0:0:C",
                                   "a:0:1:4:0:0:0:0:0:0:D"], current_idx=0)
        result = p.next_service(count=2)
        self.assertEqual([r.toString() for r in result],
                         ["a:0:1:2:0:0:0:0:0:0:B", "a:0:1:3:0:0:0:0:0:0:C"])

    def test_skip_directory_entries(self):
        # Second entry has skip flag - should not be returned.
        refs = [FakeRef("a:0:1:1:0:0:0:0:0:0:A"),
                FakeRef("DIR:::::::", flags=0x47),
                FakeRef("a:0:1:3:0:0:0:0:0:0:C")]
        p = Predictor(
            bouquet_provider=lambda: "b",
            service_center_provider=lambda: FakeServiceCenter(refs),
            history_provider=lambda: [],
            current_service_provider=lambda: refs[0],
        )
        result = p.next_service(count=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].toString(), "a:0:1:3:0:0:0:0:0:0:C")

    def test_history_returns_recent_excluding_live(self):
        live_str = "x:0:1:X:0:0:0:0:0:0:X"
        live = FakeRef(live_str)
        h_old = FakeRef("x:0:1:1:0:0:0:0:0:0:Old")
        h_mid = FakeRef("x:0:1:2:0:0:0:0:0:0:Mid")
        h_curr = FakeRef(live_str)  # live also appears in history; must be filtered
        p = Predictor(
            bouquet_provider=lambda: "b",
            service_center_provider=lambda: FakeServiceCenter([]),
            history_provider=lambda: [h_old, h_mid, h_curr],
            current_service_provider=lambda: live,
        )
        result = p.history_service(count=2)
        # history reversed: most recent first after dropping live
        self.assertEqual([r.toString() for r in result],
                         ["x:0:1:2:0:0:0:0:0:0:Mid", "x:0:1:1:0:0:0:0:0:0:Old"])

    def test_history_deduplicates(self):
        live = FakeRef("x:0:1:X:0:0:0:0:0:0:X")
        a = FakeRef("x:0:1:1:0:0:0:0:0:0:A")
        b = FakeRef("x:0:1:1:0:0:0:0:0:0:A_renamed")  # same key (renamed only)
        p = Predictor(
            bouquet_provider=lambda: "b",
            service_center_provider=lambda: FakeServiceCenter([]),
            history_provider=lambda: [a, b],
            current_service_provider=lambda: live,
        )
        result = p.history_service(count=5)
        self.assertEqual(len(result), 1)

    def test_count_zero_returns_empty(self):
        p, _ = make_predictor(["a:0:1:1:0:0:0:0:0:0:A",
                                "a:0:1:2:0:0:0:0:0:0:B"])
        self.assertEqual(p.next_service(count=0), [])
        self.assertEqual(p.prev_service(count=0), [])
        self.assertEqual(p.history_service(count=0), [])


if __name__ == "__main__":
    unittest.main()
