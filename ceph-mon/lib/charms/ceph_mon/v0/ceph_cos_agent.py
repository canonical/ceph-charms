# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""
Library for handling ceph observability integrations
"""

import logging
import socket
import tenacity
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import charm

from charms.grafana_agent.v0 import cos_agent
from charms_ceph import utils as ceph_utils

# The unique Charmhub library identifier, never change it
LIBID = "ac526775f8ed42ebb46b231dc00519d3"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 3

logger = logging.getLogger(__name__)


class CephCOSAgentProvider(cos_agent.COSAgentProvider):

    def __init__(self, charm, refresh_cb = None, departed_cb = None):
        super().__init__(
            charm,
            metrics_rules_dir="./files/prometheus_alert_rules",
            dashboard_dirs=["./files/grafana_dashboards"],
            scrape_configs=self._custom_scrape_configs,
        )
        self._refresh_cb = refresh_cb
        self._departed_cb = departed_cb

        events = self._charm.on[cos_agent.DEFAULT_RELATION_NAME]
        self.framework.observe(
            events.relation_departed, self._on_relation_departed
        )

    def _on_refresh(self, event):
        """Enable prometheus on relation change"""
        super()._on_refresh(event)
        
        if not self._charm.unit.is_leader():
            logger.debug("Not the charm leader, skipping refresh cb.")
            return

        if callable(self._refresh_cb):
            self._refresh_cb(event) 
        else:
            # ceph mon failback
            if not ceph_utils.is_bootstrapped():
                logger.debug("not bootstrapped, defer _on_refresh: %s", event)
                event.defer()
                return

            logger.debug("refreshing cos_agent relation")
            ceph_utils.mgr_enable_module("prometheus")

        self.mgr_config_set_rbd_stats_pools()

    def _on_relation_departed(self, event):
        """Disable prometheus on depart of relation"""
        if self._charm.unit.is_leader():
            logger.debug("Not the charm leader, skipping relation_departed: %s.", event)

        if callable(self._departed_cb):
            self._departed_cb(event)
        else:
            # ceph mon fallback
            if ceph_utils.is_bootstrapped():
                logger.debug(
                    "is_leader and is_bootstrapped, running rel departed: %s",
                    event,
                )
                ceph_utils.mgr_disable_module("prometheus")
        logger.debug("module_disabled")

    def _custom_scrape_configs(self):
        fqdn = socket.getfqdn()
        fqdn_parts = fqdn.split('.')
        domain = '.'.join(fqdn_parts[1:]) if len(fqdn_parts) > 1 else fqdn
        return [
            {
                "metrics_path": "/metrics",
                "static_configs": [{"targets": ["localhost:9283"]}],
                "honor_labels": True,
                "metric_relabel_configs": [
                    {
                        # localhost:9283 is the generic default instance label
                        # added by grafana-agent which is kinda useless.
                        # Replace it with a somewhat more meaningful label
                        "source_labels": ["instance"],
                        "regex": "^localhost:9283$",
                        "target_label": "instance",
                        "action": "replace",
                        "replacement": "ceph_cluster",
                    },
                    {   # if we have a non-empty hostname label, use it as the
                        # instance label
                        "source_labels": ["hostname"],
                        "regex": "(.+)",
                        "target_label": "instance",
                        "action": "replace",
                        "replacement": "${1}",
                    },
                    {   # tack on the domain to the instance label to make it
                        # conform to grafana-agent's node-exporter expectations
                        "source_labels": ["instance"],
                        "regex": "(.*)",
                        "target_label": "instance",
                        "action": "replace",
                        "replacement": "${1}." + domain,
                    },
                ]
            },

        ]

    # TODO: switch to charms_ceph sourced util function
    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, max=10),
        reraise=True,
        stop=tenacity.stop_after_attempt(30))
    def mgr_config_set_rbd_stats_pools(self):
        """Update ceph mgr config with the value from rbd-status-pools config
        """
        rbd_stats_pools = self._charm.model.config.get('rbd-stats-pools')
        if rbd_stats_pools:
            ceph_utils.mgr_config_set(
                'mgr/prometheus/rbd_stats_pools',
                rbd_stats_pools
            )
        enable_perf_metrics = self._charm.model.config.get('enable-perf-metrics', False)
        ceph_utils.mgr_config_set(
            'mgr/prometheus/exclude_perf_counters',
            str(not enable_perf_metrics)  # flip the charm config value 
        )
