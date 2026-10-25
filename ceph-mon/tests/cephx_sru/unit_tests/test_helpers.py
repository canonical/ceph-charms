import unittest

from helpers import pgs_active_clean


class PlacementGroupReadinessTest(unittest.TestCase):
    def test_scrubbing_does_not_make_clean_groups_unready(self):
        self.assertTrue(pgs_active_clean([
            {"state": "active+clean"},
            {"state": "active+clean+scrubbing+deep"},
        ]))

    def test_every_group_must_be_active_and_clean(self):
        for states in ([], ["active+degraded"], ["clean"],
                       ["active+clean", "peering"]):
            with self.subTest(states=states):
                self.assertFalse(pgs_active_clean([
                    {"state": state} for state in states
                ]))
