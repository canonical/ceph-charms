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

import argparse
import errno
import json
import logging
import os
import shutil
import socket
import sys
import time
import uuid

sys.path.append(os.path.dirname(os.path.abspath(__name__)))
import radosmap
import utils


NQN_BASE = 'nqn.2014-08.org.nvmexpress:uuid:'
NQN_DISCOVERY = 'nqn.2014-08.org.nvmexpress.discovery'

logger = logging.getLogger(__name__)


def _json_dumps(x):
    return json.dumps(x, separators=(',', ':'))


class ProxyError(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class ProxyCommand:
    def __init__(self, msg, fatal=False):
        self.msg = msg
        self.fatal = fatal

    def __call__(self, proxy):
        return proxy.msgloop(self.msg)


class ProxyCreateEndpoint:
    def __init__(self, msg, bdev_name, cluster):
        self.msg = msg
        self.bdev_name = bdev_name
        self.cluster = cluster

    @staticmethod
    def _check_reply(msg, proxy):
        reply = proxy.msgloop(msg)
        if proxy.is_error(reply):
            raise ValueError('%s failed: %s' % (msg['method'], reply))
        return reply

    def _add_listener(self, proxy, cleanup, **kwargs):
        params = kwargs['listen_address']
        port = params.get('trsvcid')

        if port is None:
            port, adrfam = utils.get_free_port(params['traddr'])
        else:
            _, adrfam = utils.get_adrfam(params['traddr'])

        params['adrfam'] = str(adrfam)
        params['trsvcid'] = str(port)
        payload = proxy.rpc.nvmf_subsystem_add_listener(**kwargs)
        self._check_reply(payload, proxy)
        cleanup.append(proxy.rpc.nvmf_subsystem_remove_listener(**kwargs))

    def __call__(self, proxy):
        cleanup = []
        rpc = proxy.rpc
        nqn = self.msg['nqn']
        try:
            payload = rpc.bdev_rbd_create(
                name=self.bdev_name, pool_name=self.msg['pool_name'],
                rbd_name=self.msg['rbd_name'],
                cluster_name=self.cluster, block_size=4096)
            self._check_reply(payload, proxy)
            cleanup.append(rpc.bdev_rbd_delete(name=self.bdev_name))

            payload = rpc.nvmf_create_subsystem(
                nqn=nqn, ana_reporting=True, max_namespaces=2)
            self._check_reply(payload, proxy)
            cleanup.append(rpc.nvmf_delete_subsystem(nqn=nqn))

            # Add namespace before listener so the subsystem is not yet
            # in active/listening state, which causes add_ns to fail on
            # some SPDK versions.
            payload = rpc.nvmf_subsystem_add_ns(
                nqn=nqn,
                namespace=proxy.ns_dict(self.bdev_name, nqn))
            self._check_reply(payload, proxy)

            self._add_listener(
                proxy, cleanup,
                nqn=nqn,
                listen_address=dict(trtype='tcp', traddr=self.msg['addr'],
                                    trsvcid=self.msg.get('port')))
            return payload
        except Exception:
            for call in reversed(cleanup):
                proxy.msgloop(call)
            raise


class ProxyAddHost:
    def __init__(self, msg, dhchap_key):
        self.msg = msg
        self.dhchap_key = dhchap_key

    @staticmethod
    def cleanup(proxy, path, response, fname=None):
        if fname is not None:
            proxy.msgloop(proxy.rpc.keyring_file_remove_key(name=fname))
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

        raise ProxyError(response['error'])

    def __call__(self, proxy):
        if not self.dhchap_key:
            return proxy.msgloop(self.msg)

        params = self.msg['params']
        fname = proxy.key_file_name(params['nqn'], params['host'])
        path = os.path.join(proxy.key_dir, fname)

        try:
            with open(path, 'r') as f:
                contents = f.read()
        except Exception:
            contents = None

        if contents is None:
            with open(path, 'w') as file:
                file.write(self.dhchap_key)
            os.chmod(path, 0o600)
        elif contents != self.dhchap_key:
            raise ProxyError('host already present with a different key')

        payload = proxy.rpc.keyring_file_add_key(name=fname, path=path)
        rv = proxy.msgloop(payload)
        if proxy.is_error(rv) and rv['error'].get('code') != -errno.EEXIST:
            self.cleanup(proxy, path, rv)

        payload = self.msg.copy()
        payload['params']['dhchap_key'] = fname
        rv = proxy.msgloop(payload)
        if proxy.is_error(rv) and contents is None:
            self.cleanup(proxy, path, rv, fname)

        return rv


class ProxyRemoveHost:
    def __init__(self, msg):
        self.msg = msg

    def __call__(self, proxy):
        nqn, host = self.msg['nqn'], self.msg['host']
        if host == 'any':
            payload = proxy.rpc.nvmf_subsystem_allow_any_host(
                nqn=nqn, allow_any_host=False)
            return proxy.msgloop(payload)

        payload = proxy.rpc.nvmf_subsystem_remove_host(nqn=nqn, host=host)
        rv = proxy.msgloop(payload)
        if proxy.is_error(rv):
            return rv

        fname = proxy.key_file_name(nqn, host)
        payload = proxy.rpc.keyring_file_remove_key(name=fname)
        if not proxy.is_error(proxy.msgloop(payload)):
            try:
                os.remove(os.path.join(proxy.key_dir, fname))
            except FileNotFoundError:
                pass

        return rv


class Proxy:
    def __init__(self, config_path, rpc_path, map_cls=radosmap.RadosMap):
        with open(config_path) as file:
            config = json.loads(file.read())

        self.rpc = utils.RPC()
        self.buffer = bytearray(4096 * 10)
        self.node_id = config['node-id']
        self.receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receiver.bind(('0.0.0.0', config['proxy-port']))
        self._connect(rpc_path)
        self._startup_init(config)
        wdir = os.path.dirname(config_path)
        self.key_dir = os.path.join(wdir, 'keys')
        self.local_state = self._read_local_state(wdir)

        cmds = list(self._prepare_cmds(config, map_cls))
        try:
            self._process_cmd(cmds[0])
        except ProxyError:
            # The first command is always 'nvmf_create_transport'
            # Since we're using TCP and support for it is always
            # built in, this can only fail in case the command has
            # already been applied, which can happen if the proxy
            # dies, but not SPDK. Since the global state may have
            # changed since the proxy was running, we need to
            # reinitialize SPDK as well.
            self.msgloop(self.rpc.spdk_kill_instance(sig_name='SIGHUP'))
            self._process_cmd(cmds[0])

        # Start with a fresh key directory.
        try:
            shutil.rmtree(self.key_dir)
        except FileNotFoundError:
            pass

        os.mkdir(self.key_dir)
        for cmd in cmds[1:]:
            try:
                self._process_cmd(cmd)
            except Exception:
                # Check if the failure is fatal.
                if not getattr(cmd, 'fatal', True):
                    continue
                raise

    def _connect(self, rpc_path, timeout=5 * 60):
        self.rpc_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # It may take a while for SPDK to come up, specially if
        # we're allocating huge pages, so retry the connection
        # a bit to make up for that.
        end = time.time() + timeout
        while True:
            if os.access(rpc_path, os.F_OK) or time.time() > end:
                self.rpc_sock.connect(rpc_path)
                return

            time.sleep(0.1)

    def _startup_init(self, config):
        """Configure iobuf pools and complete SPDK initialization.

        nvmf_tgt is started with --wait-for-rpc, so we must call
        iobuf_set_options (a startup-only RPC) before framework_start_init.
        """
        cpuset = utils.compute_cpuset(config.get('cpuset', ''))
        num_cores = len(cpuset)
        # SPDK defaults (1024 large, 8192 small) work reliably at 8 cores.
        # Per-core ratios: 1024/8 = 128 large, 8192/8 = 1024 small.
        large_pool = max(1024, num_cores * 128)
        small_pool = max(8192, num_cores * 1024)

        reply = self.msgloop(self.rpc.iobuf_set_options(
            small_pool_count=small_pool,
            large_pool_count=large_pool))
        if self.is_error(reply):
            logger.warning('iobuf_set_options failed: %s', reply)

        reply = self.msgloop(self.rpc.framework_start_init())
        if self.is_error(reply):
            raise RuntimeError('framework_start_init failed: %s' % reply)

    def _read_local_state(self, wdir):
        fname = os.path.join(wdir, 'local.json')
        try:
            self.local_file = open(fname, 'r+b')
            obj = json.load(self.local_file)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            if (isinstance(exc, json.JSONDecodeError) and
                    os.path.getsize(fname) > 0):
                raise

            self.local_file = open(fname, 'w+b')
            return {'version': radosmap.VERSION, 'clusters': []}

        for elem in obj.get('clusters', ()):
            self.msgloop(self.rpc.bdev_rbd_register_cluster(
                name=elem['name'], user_id=elem['user'],
                config_param={'key': elem['key'],
                              'mon_host': elem['mon_host']}))

        return obj

    def _prepare_cmds(self, config, map_cls):
        self.gmapper = map_cls(config['pool'], logger)
        yield ProxyCommand(self.rpc.nvmf_create_transport(trtype='tcp'))

        xaddr = utils.get_external_addr()
        yield ProxyCommand(self.rpc.nvmf_subsystem_add_listener(
            nqn=NQN_DISCOVERY,
            listen_address=dict(trtype='tcp', traddr=xaddr,
                                adrfam=utils.get_adrfam(xaddr)[1],
                                trsvcid=str(config['discovery-port']))))

        if not self.local_state.get('clusters'):
            return

        cluster = self.local_state['clusters'][0]
        self.gmapper.add_cluster(cluster['user'], cluster['key'],
                                 cluster['mon_host'])
        subsys = self.gmapper.get_global_map().get('subsys')
        if not subsys:
            return

        rpc = self.rpc
        empty = {}

        for nqn, elem in subsys.items():
            units = elem.get('units', empty)
            this_unit = units.get(self.node_id)

            if this_unit is None:
                continue

            bdev_name = elem['name']
            bdev_info = self._parse_bdev_name(bdev_name)

            msg = {'nqn': nqn, 'pool_name': bdev_info['pool'],
                   'rbd_name': bdev_info['image'],
                   'addr': this_unit[0], 'port': this_unit[1]}
            yield ProxyCreateEndpoint(msg, bdev_name, cluster['name'])

            del units[self.node_id]
            for unit in units.values():
                payload = rpc.nvmf_discovery_add_referral(
                    subnqn=nqn, address=dict(
                        trtype='tcp', traddr=unit[0], trsvcid=str(unit[1])))
                yield ProxyCommand(payload)

            hosts = elem.get('hosts')
            if hosts is None:
                continue

            for host in hosts:
                h, k = host['host'], host.get('dhchap_key')
                if h == 'any':
                    if not k:
                        continue
                    payload = rpc.nvmf_subsystem_allow_any_host(
                        nqn=nqn, allow_any_host=True)
                    yield ProxyCommand(payload)
                    continue

                payload = rpc.nvmf_subsystem_add_host(nqn=nqn, host=h)
                yield ProxyAddHost(payload, k)

    def get_spdk_subsystems(self):
        """Return a dictionary describing the subsystems for the gateway."""
        obj = self.msgloop(self.rpc.nvmf_get_subsystems())
        if not isinstance(obj, dict):
            logger.warning('did not receive a dict from SPDK: %s', obj)
            return

        obj = obj.get('result', ())
        ret = {}
        for elem in obj:
            nqn = elem.pop('nqn')
            if nqn is None or 'discovery' in nqn:
                continue

            ret[nqn] = elem

        return ret

    def _process_cmd(self, cmd):
        obj = cmd(self)
        if not isinstance(obj, dict):
            logger.error('invalid response received (%s - %s)',
                         type(obj), obj)
            raise TypeError()
        elif 'error' in obj:
            logger.error('error running command: %s', obj)
            raise ProxyError(obj['error'])

        return obj

    @staticmethod
    def is_error(msg):
        return not isinstance(msg, dict) or 'error' in msg

    def msgloop(self, msg):
        """Send an RPC to SPDK and receive the response."""
        self.rpc_sock.sendall(json.dumps(msg).encode('utf8'))
        nbytes = self.rpc_sock.recv_into(self.buffer)
        try:
            return json.loads(self.buffer[:nbytes])
        except Exception:
            return None

    def _get_method_handlers(self, method):
        expand = getattr(self, '_expand_' + method, None)
        post = getattr(self, '_post_' + method, None)

        if expand is None and post is not None:
            expand = lambda *_: ()   # noqa

        return expand, post

    def handle_request(self, msg, addr):
        """Handle a request from a particular client."""
        obj = json.loads(msg)
        method = obj['method'].strip()
        if method == 'stop':
            logger.debug('stopping proxy as requested')
            return True

        handler, post = self._get_method_handlers(method)
        if handler is None:
            logger.error('invalid method: %s', method)
            self.receiver.sendto(('{"error": "invalid method: %s"}' %
                                 method).encode('utf8'), addr)
            return

        logger.info('processing request: %s', obj)
        obj = obj.get('params')
        cmds = list(handler(obj))
        for cmd in cmds:
            self._process_cmd(cmd)

        resp = {}
        if post is not None:
            resp = post(obj) or {}
        self.receiver.sendto(_json_dumps(resp).encode('utf8'), addr)

    @staticmethod
    def _make_exc_msg(exc):
        if isinstance(exc, ProxyError):
            return exc.args[0]

        return {"code": -2, "type": str(type(exc)), "message": str(exc)}

    def serve(self):
        """Main server loop."""
        while True:
            inaddr = None
            try:
                nbytes, inaddr = self.receiver.recvfrom_into(self.buffer)
                logger.info('got a request from address ', inaddr)
                rv = self.handle_request(self.buffer[:nbytes], inaddr)
                if rv:
                    logger.warning('got a request to stop proxy')
                    return
            except Exception as exc:
                logger.exception('caught exception: ')
                if inaddr is not None:
                    err = {"error": self._make_exc_msg(exc)}
                    self.receiver.sendto(json.dumps(err).encode('utf8'),
                                         inaddr)

    # RPC handlers.

    @staticmethod
    def _parse_bdev_name(name):
        ix = name.find('://')
        ret = json.loads(name[ix + 3:])
        ret['type'] = name[:ix]
        return ret

    @staticmethod
    def key_file_name(nqn, host):
        # Create a unique file path for a key.
        def _normalize_nqn(val):
            return val.replace(NQN_BASE, '').replace('-', '').replace(':', '')

        return _normalize_nqn(nqn) + '@' + _normalize_nqn(host)

    @staticmethod
    def ns_dict(bdev_name, nqn):
        # In order for namespaces to be equal, the following must match:
        # namespace ID (always set to 1)
        # NGUID (32 bytes)
        # EUI64 (16 bytes)
        # UUID
        # The latter 3 are derived from the NQN, which is either allocated
        # on the fly, or passed in as a parameter.
        uuid = nqn[len(NQN_BASE):]
        base = uuid.replace('-', '')
        return dict(bdev_name=bdev_name, nsid=1, nguid=base,
                    eui64=base[:16], uuid=uuid)

    @staticmethod
    def _subsystem_to_dict(subsys):
        elem = subsys['listen_addresses'][0]
        return {'addr': elem['traddr'], 'port': elem['trsvcid'],
                'hosts': subsys['hosts'],
                'allow_any_host': subsys['allow_any_host'],
                **Proxy._parse_bdev_name(subsys['namespaces'][0]['name'])}

    def _expand_create(self, msg):
        cluster = msg['cluster']
        bdev = {'pool': msg['pool_name'], 'image': msg['rbd_name'],
                'cluster': cluster}
        bdev_name = 'rbd://' + _json_dumps(bdev)

        nqn = msg.get('nqn')
        if nqn is None:
            nqn = NQN_BASE + str(uuid.uuid4())
            msg['nqn'] = nqn   # Inject it to use it in the post handler.

        msg['bdev_name'] = bdev_name
        yield ProxyCreateEndpoint(msg, bdev_name, cluster)

    def _post_create(self, msg):
        subsystems = self.get_spdk_subsystems()
        nqn = msg['nqn']
        sub = subsystems[nqn]
        trid = sub['listen_addresses'][0]

        def _update_map(gmap):
            elem = {'name': msg['bdev_name'], 'units': {},
                    'hosts': [{'host': 'any', 'key': False}]}
            sub = gmap['subsys'].setdefault(nqn, elem)
            sub['units'].update({self.node_id: [trid['traddr'],
                                                str(trid['trsvcid'])]})

        self.gmapper.update_map(_update_map)
        return {'nqn': nqn, 'addr': trid['traddr'], 'port': trid['trsvcid']}

    def _expand_remove(self, msg):
        nqn = msg['nqn']
        name = self.get_spdk_subsystems()[nqn]['namespaces'][0]['name']
        payload = self.rpc.nvmf_subsystem_remove_ns(
            nqn=msg['nqn'], nsid=1)
        yield ProxyCommand(payload)

        payload = self.rpc.nvmf_delete_subsystem(nqn=msg['nqn'])
        yield ProxyCommand(payload)

        payload = self.rpc.bdev_rbd_delete(name=name)
        yield ProxyCommand(payload)

    def _post_remove(self, msg):
        def _update_map(gmap):
            elem = gmap['subsys'].get(msg['nqn'])
            if elem is None:
                return

            elem['units'].pop(self.node_id)

        self.gmapper.update_map(_update_map)

    def _expand_cluster_add(self, msg):
        for cluster in self.local_state.get('clusters', ()):
            if cluster['name'] == msg['name']:
                return

        payload = self.rpc.bdev_rbd_register_cluster(
            name=msg['name'], user_id=msg['user'],
            config_param={'key': msg['key'], 'mon_host': msg['mon_host']})
        yield ProxyCommand(payload)

    def _post_cluster_add(self, msg):
        logger.warning("connecting to cluster")
        self.gmapper.add_cluster(msg['user'], msg['key'], msg['mon_host'])
        self.local_state['clusters'].append(msg)
        data = json.dumps(self.local_state).encode('utf8')
        self.local_file.seek(0)
        self.local_file.write(data)
        self.local_file.truncate(len(data))

    def _expand_join(self, msg):
        nqn = msg['nqn']
        subsystems = self.get_spdk_subsystems()
        if nqn not in subsystems:
            return

        for elem in msg.get('addresses', ()):
            payload = self.rpc.nvmf_discovery_add_referral(
                subnqn=nqn, address=dict(
                    trtype='tcp', traddr=elem['addr'],
                    trsvcid=str(elem['port'])))
            yield ProxyCommand(payload)

    def _post_find(self, msg):
        subsys = self.get_spdk_subsystems().get(msg['nqn'])
        return self._subsystem_to_dict(subsys) if subsys else {}

    def _post_list(self, msg):
        subsystems = self.get_spdk_subsystems()
        return [{'nqn': nqn, **self._subsystem_to_dict(subsys)}
                for nqn, subsys in subsystems.items()]

    def _post_host_list(self, msg):
        subsys = self.get_spdk_subsystems().get(msg['nqn'])
        if subsys is None:
            return {'error': 'nqn not found'}
        elif subsys.get('allow_any_host'):
            return 'any'
        return subsys.get('hosts', [])

    def _expand_leave(self, msg):
        elems = msg.get('subsystems')
        if elems is None:
            elems = [msg]

        for subsys in elems:
            payload = self.rpc.nvmf_discovery_remove_referral(
                subnqn=subsys['nqn'],
                address=dict(
                    traddr=subsys['addr'], trsvcid=str(subsys['port']),
                    trtype='tcp'))
            yield ProxyCommand(payload, fatal=False)

    def _expand_host_add(self, msg):
        host = msg['host']
        if host == 'any':
            payload = self.rpc.nvmf_subsystem_allow_any_host(
                nqn=msg['nqn'], allow_any_host=True)
            yield ProxyCommand(payload)
        else:
            payload = self.rpc.nvmf_subsystem_add_host(
                nqn=msg['nqn'], host=host)
            yield ProxyAddHost(payload, msg.get('dhchap_key'))

    def _post_host_add(self, msg):
        def _update_map(gmap):
            elem = gmap['subsys'].get(msg['nqn'])
            if elem is None:
                self.logger.warning('host_add: NQN %s not found' % msg['nqn'])
                return

            hosts = elem['hosts']
            host = msg['host']
            if host == 'any':
                hosts[0]['key'] = True
                return

            for h in hosts:
                if h['host'] == host:
                    h['key'] = msg.get('dhchap_key')
                    return

            hosts.append({'host': host, 'key': msg.get('dhchap_key')})

        self.gmapper.update_map(_update_map)

    def _expand_host_del(self, msg):
        yield ProxyRemoveHost(msg)

    def _post_host_del(self, msg):
        def _update_map(gmap):
            elem = gmap['subsys'].get(msg['nqn'])
            if elem is None:
                self.logger.warning('host_del: NQN %s not found' % msg['nqn'])
                return

            hosts = elem['hosts']
            h = msg['host']
            if h == 'any':
                hosts[0]['key'] = False
                return

            for i, h in enumerate(hosts):
                if h['host'] == h:
                    elem['hosts'] = hosts[:i] + hosts[i + 1:]
                    return

            self.logger.warning('host %s not found' % h)


def main():
    parser = argparse.ArgumentParser(description='proxy server for SPDK')
    parser.add_argument('config', help='path to configuration file')
    parser.add_argument('-s', dest='sock', help='local socket for RPC',
                        type=str, default='/var/tmp/spdk.sock')
    args = parser.parse_args()
    proxy = Proxy(args.config, args.sock)
    proxy.serve()


if __name__ == '__main__':
    main()
