import unittest

from _enigma_stubs import bootstrap
bootstrap()

from FBCChannelSpeedChange.predictor import Predictor, is_tunable_dvb


class FakeRef:
    def __init__(self, ref_str, flags=0, type=None):
        self._s = ref_str
        self._flags = flags
        if type is not None:
            self.type = type
        else:
            try:
                self.type = int(ref_str.split(":", 1)[0])
            except (ValueError, IndexError):
                self.type = 1

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


class TunableDvbHelperTests(unittest.TestCase):
    """Unit tests for the is_tunable_dvb type check."""

    def test_dvb_ref_is_tunable(self):
        self.assertTrue(is_tunable_dvb(FakeRef("1:0:19:283D:3FB:1:C00000:0:0:0:")))

    def test_iptv_ref_is_not_tunable(self):
        # Type 4097 = idServiceMP3 (Pluto TV / other HTTP-stream refs).
        self.assertFalse(is_tunable_dvb(
            FakeRef("4097:0:1:EF:0:0:0:0:0:0:pluto%3a//abc:Star Trek")))

    def test_file_ref_is_not_tunable(self):
        # Type 4353 (0x1101) = idFile (recording playback).
        self.assertFalse(is_tunable_dvb(FakeRef("4353:0:1:0:0:0:0:0:0:0:")))

    def test_missing_type_attr_is_not_tunable(self):
        class NoType:
            def toString(self):
                return "??"
        self.assertFalse(is_tunable_dvb(NoType()))

    def test_none_is_not_tunable(self):
        self.assertFalse(is_tunable_dvb(None))


class NonDvbFilterInBouquetTests(unittest.TestCase):
    """The predictor's _list_bouquet must exclude non-DVB refs so
    NEXT / PREV never propose Pluto-TV etc. as pretune candidates.
    Root cause of the v0.6.4 crash-loop bug: recordService called
    with an IPTV ref SIGABRTs the enigma2 process.
    """

    def test_bouquet_walk_skips_iptv_neighbors(self):
        # Live is DVB, both neighbours are Pluto-TV IPTV refs.
        # The predictor must return an empty list rather than routing
        # the IPTV refs to the pretune path.
        refs = [FakeRef("1:0:19:283D:3FB:1:C00000:0:0:0:ARD"),
                FakeRef("4097:0:1:EF:0:0:0:0:0:0:pluto1"),
                FakeRef("4097:0:1:EA:0:0:0:0:0:0:pluto2")]
        p = Predictor(
            bouquet_provider=lambda: "b",
            service_center_provider=lambda: FakeServiceCenter(refs),
            history_provider=lambda: [],
            current_service_provider=lambda: refs[0],
        )
        self.assertEqual(p.next_service(count=1), [])
        self.assertEqual(p.prev_service(count=1), [])

    def test_bouquet_walk_returns_only_dvb_from_mixed_bouquet(self):
        # Mixed bouquet: one DVB neighbour, one IPTV neighbour. The
        # DVB neighbour survives, the IPTV one is filtered.
        refs = [FakeRef("1:0:19:A:0:0:0:0:0:0:live"),
                FakeRef("4097:0:1:X:0:0:0:0:0:0:pluto"),
                FakeRef("1:0:19:C:0:0:0:0:0:0:sat")]
        p = Predictor(
            bouquet_provider=lambda: "b",
            service_center_provider=lambda: FakeServiceCenter(refs),
            history_provider=lambda: [],
            current_service_provider=lambda: refs[0],
        )
        result = p.next_service(count=2)
        # Only the DVB neighbour survives - Pluto ref is skipped, the
        # walk continues past it, so the second DVB entry is returned
        # as the first (and only) candidate.
        self.assertEqual([r.toString() for r in result],
                         ["1:0:19:C:0:0:0:0:0:0:sat"])


class NonDvbFilterInHistoryTests(unittest.TestCase):
    """history_service must also skip non-DVB refs. Same crash risk:
    if the user watches Pluto and then a DVB channel, the just-departed
    Pluto ref sits in enigma2's history list and would otherwise be
    picked up as the HISTORY pretune target on the very next rearm.
    """

    def test_history_skips_iptv_ref(self):
        live = FakeRef("1:0:19:X:0:0:0:0:0:0:live")
        iptv = FakeRef("4097:0:1:E:0:0:0:0:0:0:pluto")
        p = Predictor(
            bouquet_provider=lambda: "b",
            service_center_provider=lambda: FakeServiceCenter([]),
            history_provider=lambda: [iptv],
            current_service_provider=lambda: live,
        )
        self.assertEqual(p.history_service(count=1), [])

    def test_history_returns_dvb_and_skips_iptv_from_mixed(self):
        live = FakeRef("1:0:19:X:0:0:0:0:0:0:live")
        iptv = FakeRef("4097:0:1:E:0:0:0:0:0:0:pluto")
        dvb  = FakeRef("1:0:19:A:0:0:0:0:0:0:sat")
        p = Predictor(
            bouquet_provider=lambda: "b",
            service_center_provider=lambda: FakeServiceCenter([]),
            history_provider=lambda: [dvb, iptv],  # iptv is more recent
            current_service_provider=lambda: live,
        )
        # Reversed iteration: iptv is skipped, dvb survives.
        result = p.history_service(count=2)
        self.assertEqual([r.toString() for r in result],
                         ["1:0:19:A:0:0:0:0:0:0:sat"])


if __name__ == "__main__":
    unittest.main()
