# Overview

The ceph-dashboard charm deploys the [Ceph Dashboard][upstream-ceph-dashboard],
a built-in web-based Ceph management and monitoring application. It works in
conjunction with the [openstack-loadbalancer][loadbalancer-charm] charm, which
in turn utilises the [hacluster][hacluster-charm] charm.

# Usage

## Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

#### `grafana-api-url`

Sets the URL of the Grafana API when using embedded graphs. See
[Embedded Grafana dashboards][anchor-grafana-dashboards].

#### `public-hostname`

Sets the hostname or address of the public endpoint used to access
the dashboard.

#### `enable-password-policy`

Sets whether certain password restrictions are enforced when a user
is created or changes their password.

#### `password-*`

There are a number of `password-*` options which impose constraints on which
passwords can be used. These options are ignored unless
`enable-password-policy` is set to 'True'.

## Deployment

We are assuming a pre-existing Ceph cluster.

Deploy ceph-dashboard as a subordinate to the ceph-mon charm:

    juju deploy ceph-dashboard
    juju add-relation ceph-dashboard:dashboard ceph-mon:dashboard

TLS is a requirement for this charm. Enable it by adding a relation to the
vault application:

    juju add-relation ceph-dashboard:certificates vault:certificates

See [Managing TLS certificates][cdg-tls] in the
[OpenStack Charms Deployment Guide][cdg] for more information on TLS.

> **Note**: This charm also supports TLS configuration via charm options
  `ssl_cert`, `ssl_key`, and `ssl_ca`.

### Load balancer

The dashboard is accessed via a load balancer using VIPs and implemented via
the openstack-loadbalancer and hacluster charms:

    juju deploy -n 3 --config vip=10.5.20.200 openstack-loadbalancer
    juju deploy hacluster openstack-loadbalancer-hacluster
    juju add-relation openstack-loadbalancer:ha openstack-loadbalancer-hacluster:ha

Now add a relation between the openstack-loadbalancer and ceph-dashboard
applications:

    juju add-relation ceph-dashboard:loadbalancer openstack-loadbalancer:loadbalancer

### Dashboard user

Credentials are needed to log in to the dashboard. Set these up by applying an
action to any ceph-dashboard unit. For example, to create an administrator user
called 'admin':

    juju run-action --wait ceph-dashboard/0 add-user username=admin role=administrator

The command's output will include a generated password.

The dashboard can then be accessed on the configured VIP and on port 8443:

https://10.5.20.200:8443

## Embedded Grafana dashboards

To embed Grafana dashboards within the Ceph dashboard some additional relations
are required (Grafana, Telegraf, and Prometheus are assumed to be
pre-existing):

    juju add-relation ceph-dashboard:grafana-dashboard grafana:dashboards
    juju add-relation ceph-dashboard:prometheus prometheus:website
    juju add-relation ceph-mon:prometheus prometheus:target
    juju add-relation ceph-osd:juju-info telegraf:juju-info
    juju add-relation ceph-mon:juju-info telegraf:juju-info

Grafana, Telegraf, and Prometheus should be related in the standard way:

    juju add-relation grafana:grafana-source prometheus:grafana-source
    juju add-relation telegraf:prometheus-client prometheus:target
    juju add-relation telegraf:dashboards grafana:dashboards

When Grafana is integrated with the Ceph Dashboard it requires TLS, so
add a relation to Vault (the grafana charm also supports TLS configuration via
`ssl_*` charm options):

    juju add-relation grafana:certificates vault:certificates

> **Important**: Ceph Dashboard will (silently) fail to display Grafana output
  if the client browser cannot validate the Grafana server's TLS certificate.
  Either ensure the signing CA certificate is known to the browser or, if in a
  testing environment, contact the Grafana dashboard directly and have the
  browser accept the unverified certificate.

Grafana should be configured with the following charm options:

    juju config grafana anonymous=True
    juju config grafana allow_embedding=True

The grafana charm also requires the vonage-status-panel and
grafana-piechart-panel plugins. The `install_plugins` configuration option
should be set to include URLs from which these plugins can be downloaded. They
are currently available from https://storage.googleapis.com/plugins-community.
For example:

    juju config grafana install_plugins="https://storage.googleapis.com/plugins-community/vonage-status-panel/release/1.0.11/vonage-status-panel-1.0.11.zip,https://storage.googleapis.com/plugins-community/grafana-piechart-panel/release/1.6.2/grafana-piechart-panel-1.6.2.zip"

Telegraf should be configured with the following charm option:

    juju config telegraf hostname="{host}"

> **Note**: The above command is to be invoked verbatim; no substitution is
  required.

Currently the dashboard does not autodetect the API endpoint of the Grafana
service. It needs to be provided via a configuration option:

    juju config ceph-dashboard grafana-api-url="https://<IP of grafana unit>:3000"

## Prometheus alerting

To enable alerting for an existing Prometheus service add the following
relations:

    juju add-relation ceph-dashboard:prometheus prometheus:website
    juju add-relation ceph-mon:prometheus prometheus:target
    juju add-relation ceph-dashboard:alertmanager-service prometheus-alertmanager:alertmanager-service
    juju add-relation prometheus:alertmanager-service prometheus-alertmanager:alertmanager-service

## Ceph Object storage

To enable Object storage management of an existing Ceph RADOS Gateway service
add the following relation:

    juju add-relation ceph-dashboard:radosgw-dashboard ceph-radosgw:radosgw-user

> **Note**: For Ceph versions older than Pacific the dashboard can only be
  related to a single ceph-radosgw application.

## Actions

This section lists Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions --schema ceph-dashboard`. If the
charm is not deployed then see file `actions.yaml`.

* `add-user`
* `delete-user`

# Documentation

The OpenStack Charms project maintains two documentation guides:

* [OpenStack Charm Guide][cg]: for project information, including development
  and support notes
* [OpenStack Charms Deployment Guide][cdg]: for charm usage information

# Bugs

Please report bugs on [Launchpad][lp-bugs-charm-ceph-dashboard].

<!-- LINKS -->

[juju-docs-actions]: https://juju.is/docs/working-with-actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[upstream-ceph-dashboard]: https://docs.ceph.com/en/latest/mgr/dashboard/
[cg]: https://docs.openstack.org/charm-guide
[cdg]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide
[cdg-tls]: https://docs.openstack.org/project-deploy-guide/charm-deployment-guide/latest/app-certificate-management.html
[lp-bugs-charm-ceph-dashboard]: https://bugs.launchpad.net/charm-ceph-dashboard
[anchor-grafana-dashboards]: #embedded-grafana-dashboards
[loadbalancer-charm]: https://jaas.ai/u/openstack-charmers/openstack-loadbalancer
[hacluster-charm]: https://jaas.ai/hacluster
