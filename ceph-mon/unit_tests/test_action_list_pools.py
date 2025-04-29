# Copyright 2016 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import json

from actions import list_pools
from test_utils import CharmTestCase


class ListPoolsTestCase(CharmTestCase):
    ceph_osd_dump = b"""
    {"epoch": 19, "fsid": "90e7e074-8263-11eb-9c5c-fa163eee3d70", "created":
    "2021-03-11 12:16:36.284078", "modified": "2021-03-18 10:41:23.173546",
    "flags": "sortbitwise,recovery_deletes,purged_snapdirs", "crush_version":
    7, "full_ratio": 0.95, "backfillfull_ratio": 0.9, "nearfull_ratio": 0.85,
    "cluster_snapshot": "", "pool_max": 2, "max_osd": 3,
    "require_min_compat_client": "jewel", "min_compat_client": "jewel",
    "require_osd_release": "luminous", "pools": [{"pool": 1, "pool_name":
    "test", "flags": 1, "flags_names": "hashpspool", "type": 1, "size": 3,
    "min_size": 2, "crush_rule": 0, "object_hash": 2, "pg_num": 8,
    "pg_placement_num": 8, "crash_replay_interval": 0, "last_change": "16",
     "last_force_op_resend": "0", "last_force_op_resend_preluminous": "0",
     "auid": 0, "snap_mode": "selfmanaged", "snap_seq": 0, "snap_epoch": 0,
     "pool_snaps": [], "removed_snaps": "[]", "quota_max_bytes": 0,
     "quota_max_objects": 0, "tiers": [], "tier_of": -1, "read_tier": -1,
     "write_tier": -1, "cache_mode": "none", "target_max_bytes": 0,
     "target_max_objects": 0, "cache_target_dirty_ratio_micro": 400000,
     "cache_target_dirty_high_ratio_micro": 600000,
     "cache_target_full_ratio_micro": 800000, "cache_min_flush_age": 0,
     "cache_min_evict_age": 0, "erasure_code_profile": "", "hit_set_params":
     {"type": "none"}, "hit_set_period": 0, "hit_set_count": 0,
     "use_gmt_hitset": true, "min_read_recency_for_promote": 0,
     "min_write_recency_for_promote": 0, "hit_set_grade_decay_rate": 0,
     "hit_set_search_last_n": 0, "grade_table": [], "stripe_width": 0,
     "expected_num_objects": 0, "fast_read": false, "options": {},
     "application_metadata": {"unknown": {}}}, {"pool": 2, "pool_name":
     "test2", "flags": 1, "flags_names": "hashpspool", "type": 1, "size": 3,
     "min_size": 2, "crush_rule": 0, "object_hash": 2, "pg_num": 8,
     "pg_placement_num": 8, "crash_replay_interval": 0, "last_change": "19",
     "last_force_op_resend": "0", "last_force_op_resend_preluminous": "0",
     "auid": 0, "snap_mode": "selfmanaged", "snap_seq": 0, "snap_epoch": 0,
     "pool_snaps": [], "removed_snaps": "[]", "quota_max_bytes": 0,
     "quota_max_objects": 0, "tiers": [], "tier_of": -1, "read_tier": -1,
     "write_tier": -1, "cache_mode": "none", "target_max_bytes": 0,
     "target_max_objects": 0, "cache_target_dirty_ratio_micro": 400000,
     "cache_target_dirty_high_ratio_micro": 600000,
     "cache_target_full_ratio_micro": 800000, "cache_min_flush_age": 0,
     "cache_min_evict_age": 0, "erasure_code_profile": "", "hit_set_params":
     {"type": "none"}, "hit_set_period": 0, "hit_set_count": 0,
     "use_gmt_hitset": true, "min_read_recency_for_promote": 0,
     "min_write_recency_for_promote": 0, "hit_set_grade_decay_rate": 0,
     "hit_set_search_last_n": 0, "grade_table": [], "stripe_width": 0,
     "expected_num_objects": 0, "fast_read": false, "options": {},
     "application_metadata": {"unknown": {}}}], "osds": [{"osd": 0, "uuid":
     "52755316-e15b-430f-82f6-e98f2800f979", "up": 1, "in": 1, "weight": 1.0,
     "primary_affinity": 1.0, "last_clean_begin": 0, "last_clean_end": 0,
     "up_from": 5, "up_thru": 17, "down_at": 0, "lost_at": 0, "public_addr":
     "10.5.0.21:6800/19211", "cluster_addr": "10.5.0.21:6801/19211",
     "heartbeat_back_addr": "10.5.0.21:6802/19211", "heartbeat_front_addr":
     "10.5.0.21:6803/19211", "state": ["exists", "up"]}, {"osd": 1, "uuid":
     "ac221f5d-0e99-468a-b3fd-8b3e47dcd8e3", "up": 1, "in": 1, "weight": 1.0,
     "primary_affinity": 1.0, "last_clean_begin": 0, "last_clean_end": 0,
     "up_from": 9, "up_thru": 17, "down_at": 0, "lost_at": 0, "public_addr":
     "10.5.0.5:6800/19128", "cluster_addr": "10.5.0.5:6801/19128",
     "heartbeat_back_addr": "10.5.0.5:6802/19128", "heartbeat_front_addr":
     "10.5.0.5:6803/19128", "state": ["exists", "up"]}, {"osd": 2, "uuid":
     "1e379cd3-0fb2-4645-a574-5096dc8e6f11", "up": 1, "in": 1, "weight": 1.0,
     "primary_affinity": 1.0, "last_clean_begin": 0, "last_clean_end": 0,
     "up_from": 13, "up_thru": 17, "down_at": 0, "lost_at": 0, "public_addr":
     "10.5.0.51:6800/19302", "cluster_addr": "10.5.0.51:6801/19302",
     "heartbeat_back_addr": "10.5.0.51:6802/19302", "heartbeat_front_addr":
     "10.5.0.51:6803/19302", "state": ["exists", "up"]}], "osd_xinfo":
     [{"osd": 0, "down_stamp": "0.000000", "laggy_probability": 0.0,
     "laggy_interval": 0, "features": 4611087853746454523, "old_weight": 0},
     {"osd": 1, "down_stamp": "0.000000", "laggy_probability": 0.0,
     "laggy_interval": 0, "features": 4611087853746454523, "old_weight": 0},
     {"osd": 2, "down_stamp": "0.000000", "laggy_probability": 0.0,
     "laggy_interval": 0, "features": 4611087853746454523, "old_weight": 0}],
     "pg_upmap": [], "pg_upmap_items": [], "pg_temp": [], "primary_temp": [],
     "blacklist": {}, "erasure_code_profiles": {"default": {"k": "2", "m": "1",
      "plugin": "jerasure", "technique": "reed_sol_van"}}}"""

    def setUp(self):
        super(ListPoolsTestCase, self).setUp(
            list_pools, ["check_output", "function_fail", "function_get",
                         "function_set"])
        self.function_get.return_value = "json"  # format=json
        self.check_output.return_value = self.ceph_osd_dump

    def test_getting_list_pools_without_details(self):
        """Test getting list of pools without details."""
        self.function_get.return_value = "text"
        self.check_output.return_value = b"1 test,2 test2"
        list_pools.main()
        self.function_get.assert_called_once_with("format")
        self.function_set.assert_called_once_with(
            {"message": "1 test,2 test2"})

    def test_getting_list_pools_with_details(self):
        """Test getting list of pools with details."""
        list_pools.main()
        self.function_get.assert_called_once_with("format")
        pools = json.loads(self.function_set.call_args.args[0]["message"])
        self.assertEqual(pools[0]["pool"], 1)
        self.assertEqual(pools[0]["size"], 3)
        self.assertEqual(pools[0]["min_size"], 2)
