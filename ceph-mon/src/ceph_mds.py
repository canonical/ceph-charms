"""Ceph mds library
"""

import logging
from typing import Dict

from charmhelpers.core.hookenv import leader_get
from ops import model

import charms_ceph.utils as ceph


logger = logging.getLogger(__name__)

import ceph_client


class CephMdsProvides(ceph_client.CephClientProvides):
    """Encapsulate the provides side of the Ceph MDS relation.

    Observes the mds-relation-joined hook event
    """

    charm = None
    _mds_name = None

    def __init__(self, charm):
        super().__init__(charm, "mds")
        self.charm = charm

    def _get_mds_name(self, relation: model.Relation, unit: model.Unit) -> str:
        """Retrieve mds-name from relation data."""
        unit_data = relation.data[unit]
        return unit_data.get("mds-name", relation.app.name)

    def _get_custom_relation_init_data(self) -> Dict:
        """Information required for the mds relation.

        :returns: Ceph configuration needed for the mds relation
        :rtype: dict
        """
        return {
            "fsid": leader_get("fsid"),
            "{}_mds_key".format(self._mds_name): ceph.get_mds_key(
                name=self._mds_name
            ),
        }

    def _handle_client_relation(
        self, relation: model.Relation, unit: model.Unit
    ) -> None:
        """Handle broker request and set the relation data

        :param relation: Operator relation
        :type relation: Relation
        :param unit: Unit to handle
        :type unit: Unit
        """

        self._mds_name = self._get_mds_name(relation, unit)

        logger.debug(
            "mon cluster in quorum and osds bootstrapped"
            " - providing mds client with keys"
        )
        super()._handle_client_relation(relation, unit)
