# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared operator framework code

Provide helpers for querying current status of ceph-mon units
"""
import logging
from typing import Mapping, List, Dict, TYPE_CHECKING

from ops import model, framework


logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import charm


class CephMonInfo(framework.Object):
    """Provide status information about ceph-mon.

    Information about
    - Relations
    - Peer information
    - CMR units
    """

    def __init__(self, charm: "charm.CephMonCharm"):
        super().__init__(charm, "moninfo")
        self.charm = charm

    @property
    def relations(self) -> Mapping[str, List[model.Relation]]:
        return self.charm.model.relations

    def get_peer_mons(self) -> Dict[model.Unit, model.RelationDataContent]:
        """Retrieve information about ceph-mon peer units."""
        return self._get_related_unit_data("mon")

    def get_osd_units(self) -> Dict[model.Unit, model.RelationDataContent]:
        """Retrieve information about related osd units."""
        return self._get_related_unit_data("osd")

    def _get_related_unit_data(
        self, reltype: str
    ) -> Dict[model.Unit, model.RelationDataContent]:
        rel_units = [
            unit for rel in self.relations[reltype] for unit in rel.units
        ]
        rel_data = {}
        for rel in self.relations[reltype]:
            for unit in rel_units:
                rel_data[unit] = rel.data.get(unit, {})
        return rel_data

    def remote_units(self) -> List[model.Unit]:
        """Retrieve related CMR units."""
        remotes = [
            unit
            for reltype in self.relations.values()
            for rel in reltype
            for unit in rel.units
            if unit.name.startswith("remote-")
        ]
        return remotes

    def sufficient_osds(self, minimum_osds: int = 3) -> bool:
        """
        Determine if the minimum number of OSD's have been
        bootstrapped into the cluster.

        :param expected_osds: The minimum number of OSD's required
        :return: boolean indicating whether the required number of
                 OSD's where detected.
        """
        osds = self.get_osd_units()
        bootstrapped_osds = sum(
            int(osd.get("bootstrapped-osds"))
            for osd in osds.values()
            if osd.get("bootstrapped-osds")
        )
        if bootstrapped_osds >= minimum_osds:
            return True
        return False

    def have_osd_relation(self) -> bool:
        return bool(self.relations["osd"])
