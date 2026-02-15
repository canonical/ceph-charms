#! /usr/bin/python3
import logging
import os
import shutil

from ops.main import main

import ceph_status
import ceph_mds
import charms.operator_libs_linux.v0.apt as apt
import charms.operator_libs_linux.v1.systemd as systemd
from charms.ceph_mon.v0 import ceph_cos_agent

from ops.charm import CharmEvents
from ops.framework import EventBase, EventSource, Object
import ops_openstack.core as openstack_core

""" Yield Admin Integration"""

logger = logging.getLogger(__name__)


class YieldAdminProvides(Object):
    """Abstraction for Yeild Admin Interface"""

    def __init__(
        self, charm: openstack_core.OSBaseCharm, relation_name: str = "yeild-admin"
    ):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

        self.framework.observe(
            charm.on[self.relation_name].relation_joined,
            self._on_relation_changed,
        )
        self.framework.observe(
            charm.on[self.relation_name].relation_changed, self._on_relation_changed
        )
        self.framework.observe(
            charm.on[self.relation_name].relation_departed, self._on_relation_departed
        )

    def _on_relation_changed(self, event) -> None:
        local_data = event.relation.data[self.model.app]
        fsid = local_data.get("fsid", None)
        mon_hosts = local_data.get("mon_hosts", None)
        admin_key = local_data.get("admin_key", None)

        if not fsid or not mon_hosts or not admin_key:
            logger.warning(
                f"Yield Admin relation missing provider data fsid({fsid}), mon_hosts({mon_hosts}) or admin_key."
            )
            self._populate_yield_parameters(event.relation)
            return

    def _on_relation_departed(self, event) -> None:
        pass

    def _populate_yield_parameters(self, relation):
        """Populates the necessary parameters into the integration's databag."""
        pass
