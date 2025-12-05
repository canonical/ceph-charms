#! /usr/bin/env python3
#
# Copyright 2024 Canonical Ltd
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

"""Charm the application."""

import ipaddress
import json
import logging
import os
import random
import socket
import subprocess

import interface_ceph_client.ceph_client as ceph_client
import interface_ceph_iscsi_admin_access.admin_access as admin_access
import ops

import utils

logger = logging.getLogger(__name__)

SPDK_TGT_FILE = '/lib/systemd/system/nvmf_tgt.service'
PROXY_FILE = '/lib/systemd/system/nvmf_proxy.service'
PROXY_WORKING_DIR = '/var/lib/nvme-of/'
PROXY_CMDS_FILE = os.path.join(PROXY_WORKING_DIR, 'cmds')

SYSTEMD_TEMPLATE = """
[Unit]
Description={description}
[Service]
User=root
ExecStart={path} {args}
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
"""


class CephNVMECharm(ops.CharmBase):
    """Charm the application."""

    POOL_NAME = '__nvme_globals'
    PACKAGES = ['librados-dev', 'librbd-dev', 'python3-rados', 'python3-rbd']

    CAPABILITIES = [
        "osd", "allow *",
        "mon", "allow *",
        "mgr", "allow r"]

    _stored = ops.framework.StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self.client = ceph_client.CephClientRequires(self, 'ceph-client')
        self.admin_access = admin_access.CephISCSIAdminAccessProvides(
            self, 'admin-access')
        self.rpc = utils.RPC()
        obs = self.framework.observe
        obs(self.on.start, self._on_start)
        obs(self.on.install, self._on_install)
        obs(self.on.config_changed, self.on_config_changed)
        obs(self.on.peers_relation_departed, self.on_peers_relation_departed)
        obs(self.on.create_endpoint_action, self.on_create_endpoint_action)
        obs(self.on.delete_endpoint_action, self.on_delete_endpoint_action)
        obs(self.on.join_endpoint_action, self.on_join_endpoint_action)
        obs(self.on.list_endpoints_action, self.on_list_endpoints_action)
        obs(self.on.add_host_action, self.on_add_host_action)
        obs(self.on.delete_host_action, self.on_delete_host_action)
        obs(self.on.list_hosts_action, self.on_list_hosts_action)
        obs(self.on.reset_target_action, self.on_reset_target_action)
        obs(self.on.pause_action, self.on_pause_action)
        obs(self.on.resume_action, self.on_resume_action)
        obs(self.client.on.broker_available, self._on_ceph_relation_joined)
        obs(self.client.on.pools_available, self._on_ceph_relation_changed)
        obs(self.admin_access.on.admin_access_request, self.on_admin_access)
        self._stored.set_default(installed_services=False)

    def on_peers_relation_departed(self, event):
        peers = self._peer_addrs()
        if not peers:
            return

        msg = self.rpc.leave(subsystems=self._msgloop({'method': 'list'}))
        sock = self._rpc_sock()

        for addr, *_ in peers:
            self._msgloop(msg, addr=addr, sock=sock)

    def on_admin_access(self, event):
        passwd = self.config.get('dashboard-password')
        if not passwd:
            logger.info('Defering setup due to missing password')
            event.defer()
            return

        scheme = 'http'
        self.admin_access.publish_gateway(
            socket.getfqdn(),
            'admin',
            passwd,
            scheme)

    def bind_addr(self):
        """Get the charm's local binding address."""
        try:
            addr = self.model.get_binding('peers').network.bind_address
            return str(addr)
        except Exception:
            return '0.0.0.0'

    def _rpc_sock(self):
        """Create a socket to communicate with the proxy."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('0.0.0.0', 0))
        return sock

    def _msgloop(self, msg, addr=None, sock=None):
        """Send an RPC message and receive a response."""
        orig_sock = sock
        if sock is None:
            sock = self._rpc_sock()

        binmsg = json.dumps(msg).encode('utf8')
        if addr is None:
            addr = self.bind_addr()
        sock.sendto(binmsg, (addr, self.config['proxy-port']))

        try:
            return json.loads(sock.recv(4096))
        except TimeoutError:
            return {'error': {'code': -2,
                              'message': 'timed out waiting for a response'}}
        finally:
            if orig_sock is None:
                sock.close()

    @property
    def app_name(self):
        return self.model.unit.app.name

    def _on_ceph_relation_joined(self, event):
        self.client.request_ceph_permissions(
            self.app_name, self.CAPABILITIES)
        self.client.create_replicated_pool(self.POOL_NAME)

    def _on_ceph_relation_changed(self, event):
        """Handle the Ceph relation."""
        if not self._stored.installed_services:
            logger.warning('systemd services still not running')
            event.defer()
            self.unit.status = ops.BlockedStatus('systemd services inactive')
            return

        data = self.client.get_relation_data()
        if not data:
            logger.warning('no Ceph relation data found - skipping')
            return

        relation = self.model.relations.get(self.client.relation_name)

        msg = self.rpc.cluster_add(
            user=self.app_name, key=data['key'],
            mon_host=','.join(data['mon_hosts']),
            name='ceph.%s' % next(iter(relation)).id)
        res = self._msgloop(msg, addr='127.0.0.1')
        if 'error' not in res:
            return

        try:
            if res['error']['code'] == -1:
                # Cluster creation failed because it already exists.
                return
        except Exception:
            pass

        err = str(res['error'])
        logging.error('failed to create cluster: %s' % err)
        event.defer()

    @staticmethod
    def _get_unit_addr(unit, rel_id):
        try:
            cmd = ['relation-get', '--format=json', '-r',
                   str(rel_id), '-', unit]
            out = json.loads(subprocess.check_output(cmd))
            return (out['private-address'], out['egress-subnets'],
                    out['ingress-address'])
        except subprocess.CalledProcessError:
            logger.exception('failed to get private address: ')
            return None

    def _get_peers(self):
        peers = self.model.relations.get('peers')
        if peers:
            peers = next(iter(peers))
        return peers

    def _peer_addrs(self):
        peers = self._get_peers()
        ret = []

        if peers:
            for peer in peers.units:
                addr = self._get_unit_addr(peer.name, peers.id)
                if addr is not None:
                    ret.append(addr)

        return ret

    def _select_ha_units(self, unit_spec):
        """Given a spec, pick a proper subset of peers to handle HA."""
        peers = self._get_peers()
        units = []

        if not peers:
            return units

        try:
            # If an integer is provided, select that many random units.
            cnt = int(unit_spec)
            if cnt <= 1:
                return units

            peer_units = list(peers.units - set((self.model.unit,)))
            units = random.sample(peer_units, min(cnt, len(peer_units)))
        except ValueError:
            # Select the specified units from the list.
            unit_names = unit_spec.split(',')
            units = [unit for unit in peers.units if unit.name in unit_names]

        ret = []
        for unit in units:
            addr = self._get_unit_addr(unit.name, peers.id)
            if addr is not None:
                ret.append((unit.name, addr))

        return ret

    @staticmethod
    def _exclude(lst, idx):
        return lst[0:idx] + lst[idx + 1:]

    @staticmethod
    def _event_set_create_results(event, response, units):
        event.set_results({'nqn': response['nqn'],
                           'address': response['addr'],
                           'port': response['port'],
                           'units': units})

    def _handle_ha_create(self, response, peers, msg, sock, event):
        valid = [{'addr': response['addr'], 'port': response['port'],
                  'rpc_addr': '127.0.0.1'}]

        # Tell the other peers to create the bdev, subsystem and namespace.
        for peer, (addr, _, xaddr) in peers:
            msg['params']['addr'] = xaddr
            peer_resp = self._msgloop(msg, addr=addr, sock=sock)
            if 'error' in peer_resp:
                logger.warning('peer %s failed to create bdev: %s' %
                               (peer, peer_resp['error']))
            else:
                valid.append({'addr': peer_resp['addr'], 'rpc_addr': addr,
                              'port': peer_resp['port']})

        # Now make each peer refer to the others.
        added = 0
        if len(valid) > 1:
            for i, peer in enumerate(valid):
                join_msg = self.rpc.join(nqn=response['nqn'],
                                         addresses=self._exclude(valid, i))
                peer_resp = self._msgloop(join_msg, addr=peer['rpc_addr'],
                                          sock=sock)
                if 'error' in peer_resp:
                    logger.warning('peer %s failed to refer to us: %s' %
                                   (peer, peer_resp['error']))
                else:
                    added += 1

        if not added:
            logger.warning('failed to create additional endpoints for HA')
        self._event_set_create_results(event, response, added or 1)

    def _select_addr(self, subnet=None):
        if subnet is None:
            return self.bind_addr()

        try:
            network = ipaddress.ip_network(subnet)
            return str(random.choice(list(network.hosts())))
        except Exception:
            return None

    def on_create_endpoint_action(self, event):
        """Handle endpoint creation."""
        relations = self.model.relations.get(self.client.relation_name)
        if not relations:
            event.fail('no Ceph relation found')
            return

        pool = event.params.get('rbd-pool')
        image = event.params.get('rbd-image')
        cluster = 'ceph.%s' % next(iter(relations)).id
        units = event.params.get('units') or "1"
        addr = self._select_addr(event.params.get('subnet'))
        if addr is None:
            event.fail('could not pick a host from subnet')
            return

        msg = self.rpc.create(pool_name=pool, rbd_name=image,
                              cluster=cluster, addr=addr)
        sock = self._rpc_sock()
        res = self._msgloop(msg, sock=sock)
        if 'error' in res:
            event.fail('failed to create endpoint: %s' % str(res['error']))
            return

        peers = self._select_ha_units(units)
        if not peers:
            if units != "1" and units != self.model.unit.name:
                logger.warning('could not get enough units for HA - '
                               'try running the "join-endpoint" action on '
                               'additional units')
            self._event_set_create_results(event, res, 1)
            return

        # The initial unit allocates the NQN on the fly, while the rest
        # of the peers need to have it set.
        msg['params']['nqn'] = res['nqn']
        self._handle_ha_create(res, peers, msg, sock, event)

    def _leave_endpoint(self, sock, nqn, elem):
        msg = self.rpc.leave(addr=elem['addr'], port=elem['port'], nqn=nqn)
        for addr, *_ in self._peer_addrs():
            # We don't care about the response here.
            self._msgloop(msg, addr=addr, sock=sock)

    def on_delete_endpoint_action(self, event):
        """Handle endpoint deletion."""
        nqn = event.params.get('nqn')
        sock = self._rpc_sock()
        elem = self._msgloop(self.rpc.find(nqn=nqn), sock=sock)
        if not elem:
            event.fail('NQN is not present on this unit')
            return

        self._leave_endpoint(sock, nqn, elem)
        res = self._msgloop(self.rpc.remove(nqn=nqn), sock=sock)
        if 'error' in res:
            event.fail('failed to remove endpoint: %s' % str(res['error']))
            return

        event.set_results({'message': 'success'})

    def _create_bdev_on_join(self, nqn, resp, sock, event):
        laddr = self._select_addr(event.params.get('subnet'))
        if laddr is None:
            raise RuntimeError('could not pick a host from subnet')

        # The response contains the necessary parameters to create the bdev.
        create_msg = self.rpc.create(nqn=nqn, pool_name=resp['pool'],
                                     rbd_name=resp['image'],
                                     cluster=resp['cluster'], addr=laddr)
        rv = self._msgloop(create_msg, addr='127.0.0.1', sock=sock)
        if 'error' in rv:
            raise RuntimeError('failed to create endpoint: %s' %
                               str(rv['error']))
        # With the endpoint created, add the hosts, if any.
        if resp.get('allow_any_host'):
            # This call cannot fail.
            self._msgloop(self.rpc.host_add(nqn=nqn, host='any'))
        else:
            for elem in resp.get('hosts', ()):
                tmp = self._msgloop(self.rpc.host_add(
                    nqn=nqn, host=elem['nqn'],
                    dhchap_key=elem.get('dhchap_key')))
                if 'error' in tmp:
                    self._msgloop(self.rpc.remove(nqn=nqn), sock=sock)
                    raise RuntimeError('failed to create endpoint: %s' %
                                       str(tmp['error']))

        return rv

    def _handle_join_peers(self, msg, nqn, peers, num_max, event):
        sock = self._rpc_sock()
        bdev_spec = None
        joined = 0

        for addr, *_ in peers:
            resp = self._msgloop(msg, addr=addr, sock=sock)
            if not resp:
                # Empty response means this peer doesn't handle the NQN.
                continue

            if bdev_spec is None:
                # Create the endpoint if we haven't already.
                try:
                    bdev_spec = self._create_bdev_on_join(nqn, resp,
                                                          sock, event)
                except Exception as exc:
                    event.fail(str(exc))
                    return 0, bdev_spec

            # Tell our peer to join us.
            alist = [{'addr': bdev_spec['addr'], 'port': bdev_spec['port']}]
            join_msg = self.rpc.join(nqn=nqn, addresses=alist)
            rv = self._msgloop(join_msg, addr=addr, sock=sock)
            if 'error' in rv:
                logger.warning('peer %s failed to refer to us: %s' %
                               (addr, rv['error']))
                continue

            # On success, join our peer.
            alist[0] = {'addr': addr, 'port': resp['port']}
            rv = self._msgloop(join_msg, addr='127.0.0.1', sock=sock)
            if 'error' in rv:
                logger.warning('could not join peer %s: %s' %
                               (addr, rv['error']))
                leave_msg = self.rpc.leave(
                    nqn=nqn, addr=bdev_spec['addr'], port=bdev_spec['port'])
                self._msgloop(leave_msg, addr=addr, sock=sock)
            else:
                joined += 1
                if joined >= num_max:
                    break

        return joined, bdev_spec

    def on_join_endpoint_action(self, event):
        """Join an endpoint."""
        peers = self._peer_addrs()
        if not peers:
            event.fail('no peers to join')
            return

        nqn = event.params.get('nqn')
        num_max = event.params.get('nmax', -1)
        if num_max <= 0:
            num_max = len(peers)

        tmp = self._msgloop(self.rpc.find(nqn=nqn), addr='127.0.0.1')
        if tmp and 'error' not in tmp:
            event.fail('unit already handles NQN')
            return

        msg = self.rpc.find(nqn=nqn)
        joined, bdev = self._handle_join_peers(msg, nqn, peers, num_max, event)

        if not joined:
            if bdev is None:
                event.fail('NQN not found')
                return
            logger.warning('endpoint created but could not join any units')
            self._msgloop(self.rpc.remove(nqn=bdev['nqn']))
            return
        self._event_set_create_results(event, bdev, joined)

    def on_list_endpoints_action(self, event):
        elems = self._msgloop({'method': 'list'}, addr='127.0.0.1')
        event.set_results({'endpoints': elems})

    def on_add_host_action(self, event):
        nqn = event.params.get('nqn')
        key = event.params.get('dhchap-key')
        kwargs = dict(nqn=nqn, host=event.params.get('hostnqn'))
        if key:
            kwargs['dhchap_key'] = key

        msg = self.rpc.host_add(**kwargs)
        sock = self._rpc_sock()
        res = self._msgloop(msg, sock=sock)
        if 'error' in res:
            event.fail('Failed to add host: %s' % res['error'])
            return

        for addr, *_ in self._peer_addrs():
            self._msgloop(msg, addr=addr, sock=sock)

        event.set_results({'message': 'success'})

    def on_delete_host_action(self, event):
        nqn = event.params.get('nqn')
        host = event.params.get('hostnqn')
        msg = self.rpc.host_del(nqn=nqn, host=host)
        sock = self._rpc_sock()
        res = self._msgloop(msg, sock=sock)

        if 'error' in res:
            event.fail('Host %s or NQN %s not found' % (host, nqn))
            return

        for addr, *_ in self._peer_addrs():
            self._msgloop(msg, addr=addr, sock=sock)

        event.set_results({'message': 'success'})

    def on_list_hosts_action(self, event):
        nqn = event.params.get('nqn')
        res = self._msgloop(self.rpc.host_list(nqn=nqn), addr='127.0.0.1')

        if 'error' in res:
            event.fail('NQN %s not found' % nqn)
            return

        event.set_results({'hosts': res})

    def _pause_or_resume(self, cmd, event):
        try:
            subprocess.check_call(['sudo', 'systemctl', cmd, 'nvmf_tgt'])
            subprocess.check_call(['sudo', 'systemctl', cmd, 'nvmf_proxy'])
            return True
        except Exception as exc:
            event.fail('Failed to %s services: %s' % (cmd, str(exc)))
            return False

    def _pause(self, event):
        return self._pause_or_resume('stop', event)

    def _resume(self, event):
        utils.setup_hugepages(int(self.config['nr-hugepages']))
        return self._pause_or_resume('start', event)

    def on_reset_target_action(self, event):
        sock = self._rpc_sock()
        elems = self._msgloop({'method': 'list'}, sock=sock)
        for elem in elems:
            # For every endpoint that we support:
            # - Tell others that we're leaving
            # - Destroy the subsystem and remove the entry from the global map.
            self._leave_endpoint(sock, elem['nqn'], elem)
            self._msgloop(self.rpc.remove(nqn=elem['nqn']), sock=sock)

        if self._pause(event):
            self._resume(event)

    def on_pause_action(self, event):
        self._pause(event)

    def on_resume_action(self, event):
        self._resume(event)

    def _install_packages(self, packages):
        # Code taken from charmhelpers.
        cmd = ['sudo', 'apt-get', '--assume-yes',
               '--option=Dpkg::Options::=--force-confold',
               'install'] + packages
        subprocess.check_call(cmd)

    def _render_config(self):
        config_path = os.path.join(PROXY_WORKING_DIR, 'config.json')
        with open(config_path, 'w') as config_file:
            conf = {k: self.config.get(k, '')
                    for k in ('cpuset', 'proxy-port',
                              'nr-hugepages', 'discovery-port')}
            conf['node-id'] = utils.node_id()
            conf['pool'] = self.POOL_NAME
            config_file.write(json.dumps(conf))
        return config_path

    def on_config_changed(self, event):
        self._render_config()

    def _install_systemd_services(self):
        self._install_packages(self.PACKAGES)
        config_path = self._render_config()
        utils.setup_hugepages(int(self.config['nr-hugepages']))

        charm_dir = os.environ.get('JUJU_CHARM_DIR', './')
        nvmf_path = os.path.realpath(charm_dir + '/spdk/build/bin/nvmf_tgt')
        nvmf_tgt = os.path.realpath(charm_dir + '/src/nvmf.py')
        utils.create_systemd_svc(SPDK_TGT_FILE, SYSTEMD_TEMPLATE,
                                 description='NVMe-oF target',
                                 path=nvmf_tgt,
                                 args=' '.join([nvmf_path, config_path]))

        proxy_path = os.path.realpath(charm_dir + '/src/proxy.py')
        utils.create_systemd_svc(PROXY_FILE, SYSTEMD_TEMPLATE,
                                 description='proxy server for target',
                                 path=proxy_path,
                                 args=config_path)

    def _on_install(self, event):
        """Handle charm installation."""
        utils.create_dir(PROXY_WORKING_DIR)
        if not self._stored.installed_services:
            self._install_systemd_services()
            self._stored.installed_services = True

    def _on_start(self, event: ops.StartEvent):
        """Handle start event."""
        self._on_install(event)
        self.unit.status = ops.ActiveStatus('ready')


if __name__ == "__main__":
    ops.main(CephNVMECharm)
