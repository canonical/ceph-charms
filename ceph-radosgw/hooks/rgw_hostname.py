# Copyright 2026 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Helpers for handling ceph-radosgw public hostnames."""


def parse_rgw_public_hostnames(hostnames):
    """Split an os-public-hostname value into a list of unique hostnames."""
    if not hostnames:
        return []

    parsed = []
    for hostname in hostnames.split(','):
        hostname = hostname.strip()
        if hostname and hostname not in parsed:
            parsed.append(hostname)
    return parsed


def normalise_rgw_public_hostnames(hostnames):
    """Return a normalised comma-separated hostname list."""
    parsed = parse_rgw_public_hostnames(hostnames)
    if parsed:
        return ','.join(parsed)
    return hostnames


def apache_ssl_vhosts(endpoints, hostnames, virtual_hosted_bucket_enabled):
    """Expand Apache endpoints into per-hostname virtual host definitions.

    The base ApacheSSLContext returns endpoint tuples containing the literal
    endpoint string. For a comma-separated os-public-hostname we need one
    vhost per hostname rather than one vhost with the full unsplit string.

    :param endpoints: Base endpoint tuples from ApacheSSLContext.
    :type endpoints: List[Tuple[str, str, int, int]]
    :param hostnames: Raw os-public-hostname value.
    :type hostnames: str
    :param virtual_hosted_bucket_enabled: Whether wildcard aliases are needed.
    :type virtual_hosted_bucket_enabled: bool
    :returns: Expanded endpoint dictionaries for template rendering.
    :rtype: List[dict]
    """
    parsed = parse_rgw_public_hostnames(hostnames)
    normalised = normalise_rgw_public_hostnames(hostnames)
    expanded = []

    for address, endpoint, ext_port, int_port in endpoints:
        endpoint_hostnames = [endpoint]
        if (parsed and
                normalise_rgw_public_hostnames(endpoint) == normalised):
            endpoint_hostnames = parsed

        for endpoint_hostname in endpoint_hostnames:
            aliases = []
            if (virtual_hosted_bucket_enabled and
                    address != endpoint_hostname):
                aliases.append('*.{}'.format(endpoint_hostname))
            expanded.append({
                'address': address,
                'endpoint': endpoint_hostname,
                'cert_cn': endpoint_hostname,
                'aliases': aliases,
                'ext': ext_port,
                'int': int_port,
            })

    return expanded


def certificate_requests(cert_requests, hostnames,
                         virtual_hosted_bucket_enabled=False):
    """Normalise certificate requests for multiple public hostnames.

    The generic certificate request helper only knows about a single hostname
    override and therefore treats a comma-separated list as one CN. Rework that
    request into one certificate request per hostname.

    :param cert_requests: Decoded cert_requests mapping.
    :type cert_requests: dict
    :param hostnames: Raw os-public-hostname value.
    :type hostnames: str
    :param virtual_hosted_bucket_enabled: Whether wildcard SANs are required.
    :type virtual_hosted_bucket_enabled: bool
    :returns: Updated certificate request mapping.
    :rtype: dict
    """
    parsed = parse_rgw_public_hostnames(hostnames)
    if not parsed:
        return cert_requests

    normalised = normalise_rgw_public_hostnames(hostnames)
    request_key = next(
        (key for key in cert_requests
         if normalise_rgw_public_hostnames(key) == normalised),
        None,
    )
    if request_key is None:
        return cert_requests

    updated_requests = dict(cert_requests)
    base_request = updated_requests.pop(request_key)
    normalised_wildcard = '*.{}'.format(normalised)
    base_sans = [
        san for san in base_request.get('sans', [])
        if normalise_rgw_public_hostnames(san) not in (
            normalised, normalised_wildcard)
    ]

    for hostname in parsed:
        sans = list(base_sans)
        if hostname not in sans:
            sans.append(hostname)
        if virtual_hosted_bucket_enabled:
            wildcard = '*.{}'.format(hostname)
            if wildcard not in sans:
                sans.append(wildcard)
        updated_requests[hostname] = {'sans': sorted(set(sans))}

    return updated_requests
