# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Provide status checking for the ceph-mon charm"""

import logging
from typing import Union, TYPE_CHECKING

from charmhelpers.core.hookenv import (
    application_version_set,
    is_relation_made,
)
from charmhelpers.fetch import get_upstream_version
from ops import model

import utils

if TYPE_CHECKING:
    import charm

from charmhelpers.contrib.storage.linux import ceph as ch_ceph

import charms_ceph.utils as ceph_utils
import ceph_shared

logger = logging.getLogger(__name__)

VERSION_PACKAGE = "ceph-common"


class StatusAssessor(ceph_shared.CephMonInfo):
    """Status checking for ceph-mon charms

    Takes a ceph-mon charm object as a client, registers checking methods for
    the charm object and updates status.
    """

    def __init__(self, charm: "charm.CephMonCharm"):
        super().__init__(charm)
        self.framework.observe(
            self.framework.on.commit, self.assess_status
        )
        self.register_checks()

    def config(self, key) -> Union[str, int, float, bool, None]:
        return self.charm.model.config.get(key)

    def check_insecure_cmr(self) -> model.StatusBase:
        if not self.config("permit-insecure-cmr") and self.remote_units():
            return model.BlockedStatus("Unsupported CMR relation")
        return model.ActiveStatus()

    def check_bootstrap_source(self) -> model.StatusBase:
        if not self.config("no-bootstrap") and is_relation_made(
            "bootstrap-source"
        ):
            return model.BlockedStatus(
                "Cannot join the bootstrap-source relation when "
                "no-bootstrap is False",
            )
        return model.ActiveStatus()

    def check_moncount(self) -> model.StatusBase:
        moncount = self.config("monitor-count")
        if (
            len(self.get_peer_mons()) + 1 < moncount
        ):  # we're including ourselves
            return model.BlockedStatus(
                "Insufficient peer units to bootstrap"
                " cluster (require {})".format(moncount)
            )
        return model.ActiveStatus()

    def check_ready_mons(self) -> model.StatusBase:
        moncount = self.config("monitor-count")
        mons = self.get_peer_mons()
        ready = sum(
            1 for mon in mons.values() if mon.get("ceph-public-address")
        )
        if ready + 1 < moncount:  # "this" mon is ready presumably
            return model.WaitingStatus(
                "Peer units detected, waiting for addresses"
            )
        return model.ActiveStatus()

    def check_rbd_features(self) -> model.StatusBase:
        configured_rbd_features = self.config("default-rbd-features")
        if utils.has_rbd_mirrors() and configured_rbd_features:
            if (
                utils.add_rbd_mirror_features(configured_rbd_features)
                != configured_rbd_features
            ):
                # The configured RBD features bitmap does not contain the
                # features required for RBD Mirroring
                return model.BlockedStatus(
                    "Configuration mismatch: RBD Mirroring "
                    "enabled but incorrect value set for "
                    "``default-rbd-features``",
                )
        return model.ActiveStatus()

    def check_get_osd_settings(self):
        try:
            ch_ceph.get_osd_settings("client")
        except ch_ceph.OSD_SETTING_EXCEPTIONS as e:
            return model.BlockedStatus(str(e))
        return model.ActiveStatus()

    def check_alert_rule_errors(self):
        if self.charm.metrics_endpoint.have_alert_rule_errors():
            return model.BlockedStatus("invalid alert rules, check unit logs")
        return model.ActiveStatus()

    def check_expected_osd_count(self):
        if ceph_utils.is_bootstrapped() and ceph_utils.is_quorum():
            expected_osd_count = self.config("expected-osd-count") or 3
            if self.sufficient_osds(expected_osd_count):
                return model.ActiveStatus("Unit is ready and clustered")
            elif not self.have_osd_relation():
                return model.BlockedStatus("Missing relation: OSD")
            else:
                return model.WaitingStatus(
                    "Monitor bootstrapped but waiting for number of"
                    " OSDs to reach expected-osd-count ({})".format(
                        expected_osd_count
                    )
                )
        else:
            return model.BlockedStatus("Unit not clustered (no quorum)")

    def register_checks(self):
        checkers = [
            self.check_insecure_cmr,
            self.check_bootstrap_source,
            self.check_moncount,
            self.check_ready_mons,
            self.check_rbd_features,
            self.check_alert_rule_errors,
            self.check_expected_osd_count,
        ]
        for check in checkers:
            self.charm.register_status_check(check)

    def assess_status(self, _event):
        logger.debug("Running assess_status() for %s", self.charm)
        application_version_set(get_upstream_version(VERSION_PACKAGE))
        self.charm.update_status()
