#!/usr/bin/env python3
# Copyright 2021 OpenStack Charmers
# See LICENSE file for licensing details.

import json
import logging
import manager
import subprocess
from typing import Dict, List, Optional
import tempfile
import uuid

logger = logging.getLogger(__name__)


# TODO: Add ACL with kerberos


class Export(object):
    """Object that encodes and decodes Ganesha export blocks"""

    def __init__(self, export_options: Optional[Dict] = None):
        if export_options is None:
            export_options = {}
        if isinstance(export_options, Export):
            raise RuntimeError('export_options must be a dictionary')
        self.export_options = export_options
        if not isinstance(self.export_options['EXPORT']['CLIENT'], list):
            self.export_options['EXPORT']['CLIENT'] = [
                self.export_options['EXPORT']['CLIENT']
            ]

    def from_export(export: str) -> 'Export':
        return Export(export_options=manager.parseconf(export))

    def to_export(self) -> str:
        return manager.mkconf(self.export_options)

    @property
    def name(self):
        if self.path:
            return self.path.split('/')[-2]

    @property
    def export(self):
        return self.export_options['EXPORT']

    @property
    def clients(self) -> List[Dict[str, str]]:
        return self.export_options['EXPORT']['CLIENT']

    @property
    def clients_by_mode(self):
        clients_by_mode = {'r': [], 'rw': []}
        for client in self.clients:
            if client['Access_Type'].lower() == 'r':
                clients_by_mode['r'] += [
                    s.strip() for s in client['Clients'].split(',')
                ]
            elif client['Access_Type'].lower() == 'rw':
                clients_by_mode['rw'] += [
                    s.strip() for s in client['Clients'].split(',')
                ]
            else:
                raise RuntimeError("Invalid access type")
        return clients_by_mode

    @property
    def export_id(self) -> int:
        return int(self.export_options['EXPORT']['Export_Id'])

    @property
    def path(self) -> str:
        return self.export_options['EXPORT']['Path']

    def add_client(self, client: str):
        mode = "rw"
        clients_by_mode = self.clients_by_mode
        logging.info(f"About to add {client} to {clients_by_mode}")
        if client not in clients_by_mode[mode.lower()]:
            clients_by_mode[mode.lower()].append(client)
        logging.info(f"new clients_by_mode: to {clients_by_mode}")
        self.export_options['EXPORT']['CLIENT'] = []
        for (mode, clients) in clients_by_mode.items():
            if clients:
                logging.info(f"Adding {clients} to self.export_options")
                self.export_options['EXPORT']['CLIENT'].append(
                    {'Access_Type': mode, 'Clients': ', '.join(clients)})

    def remove_client(self, client: str):
        clients_by_mode = self.clients_by_mode
        for (mode, clients) in clients_by_mode.items():
            clients_by_mode[mode] = [
                old_client for old_client in clients if old_client != client
            ]
        self.export_options['EXPORT']['CLIENT'] = []
        for (mode, clients) in clients_by_mode.items():
            if clients:
                self.export_options['EXPORT']['CLIENT'].append(
                    {'Access_Type': mode, 'Clients': ', '.join(clients)})


class GaneshaNFS(object):
    export_index = "ganesha-export-index"
    export_counter = "ganesha-export-counter"

    def __init__(self, client_name, ceph_pool):
        self.client_name = client_name
        self.ceph_pool = ceph_pool

    def create_share(self, name: str = None, size: int = None,
                     access_ips: List[str] = None) -> str:
        """Create a CephFS Share and export it via Ganesha

        :param name: String name of the share to create
        :param size: Int size in gigabytes of the share to create

        :returns: Path to the export
        """
        if name is None:
            name = str(uuid.uuid4())
        else:
            existing_shares = [
                share for share in self.list_shares() if share.name == name
            ]
            if existing_shares:
                return existing_shares[0].path
        if size is not None:
            size_in_bytes = size * 1024 * 1024 * 1024
        if access_ips is None:
            access_ips = ['0.0.0.0']
        # Ganesha deals with networks just fine, except when the network is
        # 0.0.0.0/0, then it has to be 0.0.0.0 which works as expected :-/
        if '0.0.0.0/0' in access_ips:
            access_ips[access_ips.index('0.0.0.0/0')] = '0.0.0.0'

        access_id = 'ganesha-{}'.format(name)
        path = self._create_cephfs_share(name, size_in_bytes)
        if not path:
            return
        self.export_path = path
        export_id = self._get_next_export_id()
        export = Export(
            {
                'EXPORT': {
                    'Export_Id': export_id,
                    'Path': self.export_path,
                    'FSAL': {
                        'Name': 'Ceph',
                        'User_Id': access_id,
                        'Secret_Access_Key': self._ceph_auth_key(access_id)
                    },
                    'Pseudo': self.export_path,
                    'Squash': 'None',
                    'CLIENT': [
                        {
                            'Access_Type': 'RW',
                            'Clients': ', '.join(access_ips),
                        }
                    ]
                }
            }
        )
        export_template = export.to_export()
        logging.debug("Export template::\n{}".format(export_template))
        tmp_file = self._tmpfile(export_template)
        self._rados_put('ganesha-export-{}'.format(export_id), tmp_file.name)
        self._ganesha_add_export(self.export_path, tmp_file.name)
        self._add_share_to_index(export_id)
        return self.export_path

    def list_shares(self) -> List[Export]:
        share_urls = [
            url.replace('%url rados://{}/'.format(self.ceph_pool), '')
            for url
            in self._rados_get('ganesha-export-index').splitlines()]
        exports_raw = [
            self._rados_get(url)
            for url in share_urls
            if url.strip()
        ]
        exports = []
        for export_raw in exports_raw:
            try:
                exports.append(Export.from_export(export_raw))
            except RuntimeError:
                logging.warning("Encountered an independently created export")
        return exports

    def resize_share(self, name: str, size: int):
        size_in_bytes = size * 1024 * 1024 * 1024
        self._ceph_subvolume_command('resize', 'ceph-fs', name,
                                     str(size_in_bytes), '--no_shrink')

    def delete_share(self, name: str, purge=False):
        share = [share for share in self.list_shares() if share.name == name]
        if share:
            share = share[0]
        else:
            return
        logging.info("About to remove export {} ({})"
                     .format(share.name, share.export_id))
        self._ganesha_remove_export(share.export_id)
        logging.debug("Removing export from index")
        self._remove_share_from_index(share.export_id)
        logging.debug("Removing export file from RADOS")
        self._rados_rm('ganesha-export-{}'.format(share.export_id))
        if purge:
            self._delete_cephfs_share(name)

    def grant_access(self, name: str, client: str) -> Optional[str]:
        share = self.get_share(name)
        if share is None:
            return 'Share does not exist'
        share.add_client(client)
        export_template = share.to_export()
        logging.debug("Export template::\n{}".format(export_template))
        tmp_file = self._tmpfile(export_template)
        self._rados_put('ganesha-export-{}'.format(share.export_id),
                        tmp_file.name)
        self._ganesha_update_export(share.export_id, tmp_file.name)

    def revoke_access(self, name: str, client: str):
        share = self.get_share(name)
        if share is None:
            return 'Share does not exist'
        share.remove_client(client)
        export_template = share.to_export()
        logging.debug("Export template::\n{}".format(export_template))
        tmp_file = self._tmpfile(export_template)
        self._rados_put('ganesha-export-{}'.format(share.export_id),
                        tmp_file.name)
        self._ganesha_update_export(share.export_id, tmp_file.name)

    def get_share(self, name: str) -> Optional[Export]:
        share = [share for share in self.list_shares() if share.name == name]
        if share:
            return share[0]

    def update_share(self, id):
        pass

    def _ganesha_add_export(self, export_path: str, tmp_path: str):
        """Add a configured NFS export to Ganesha"""
        self._dbus_send(
            'ExportMgr', 'AddExport',
            'string:{}'.format(tmp_path),
            'string:EXPORT(Path={})'.format(export_path))

    def _ganesha_remove_export(self, share_id: int):
        """Remove a configured NFS export from Ganesha"""
        self._dbus_send(
            'ExportMgr',
            'RemoveExport',
            "uint16:{}".format(share_id))

    def _ganesha_update_export(self, share_id: int, tmp_path: str):
        """Update a configured NFS export in Ganesha"""
        self._dbus_send(
            'ExportMgr', 'UpdateExport',
            'string:{}'.format(tmp_path),
            'string:EXPORT(Export_Id={})'.format(share_id))

    def _dbus_send(self, section: str, action: str, *args):
        """Send a command to Ganesha via Dbus"""
        cmd = [
            'dbus-send', '--print-reply', '--system',
            '--dest=org.ganesha.nfsd',
            '/org/ganesha/nfsd/{}'.format(section),
            'org.ganesha.nfsd.exportmgr.{}'.format(action)] + [*args]
        logging.debug("About to call: {}".format(cmd))
        return subprocess.check_output(cmd)

    def _delete_cephfs_share(self, name: str):
        """Delete a CephFS share.

        :param name: String name of the share to create
        """
        self._ceph_subvolume_command(
            'deauthorize', 'ceph-fs', name,
            'ganesha-{name}'.format(name=name))
        self._ceph_subvolume_command('rm', 'ceph-fs', name)

    def _create_cephfs_share(self, name: str, size_in_bytes: int = None):
        """Create an authorise a CephFS share.

        :param name: String name of the share to create
        :param size_in_bytes: Integer size in bytes of the size to create

        :returns: export path
        :rtype: union[str, bool]
        """
        try:
            if size_in_bytes is not None:
                self._ceph_subvolume_command('create', 'ceph-fs',
                                             name, str(size_in_bytes))
            else:
                self._ceph_subvolume_command('create', 'ceph-fs', name)
        except subprocess.CalledProcessError:
            logging.error("failed to create subvolume")
            return False

        try:
            self._ceph_subvolume_command(
                'authorize', 'ceph-fs', name,
                'ganesha-{name}'.format(name=name))
        except subprocess.CalledProcessError:
            logging.error("failed to authorize subvolume")
            return False

        try:
            output = self._ceph_subvolume_command('getpath', 'ceph-fs', name)
            return output.decode('utf-8').strip()
        except subprocess.CalledProcessError:
            logging.error("failed to get path")
            return False

    def _ceph_subvolume_command(
        self, *cmd: List[str]
    ) -> subprocess.CompletedProcess:
        """Run a ceph fs subvolume command"""
        return self._ceph_fs_command('subvolume', *cmd)

    def _ceph_fs_command(self, *cmd: List[str]) -> subprocess.CompletedProcess:
        """Run a ceph fs command"""
        return self._ceph_command('fs', *cmd)

    def _ceph_auth_key(self, access_id: str) -> str:
        """Retrieve the CephX key associated with this id

        :returns: The access key
        :rtype: str
        """
        output = self._ceph_command(
            'auth', 'get', 'client.{}'.format(access_id), '--format=json')
        return json.loads(output.decode('UTF-8'))[0]['key']

    def _ceph_command(self, *cmd: List[str]) -> subprocess.CompletedProcess:
        """Run a ceph command"""
        cmd = [
            "ceph", "--id", self.client_name,
            "--conf=/etc/ceph/ceph.conf"
        ] + [*cmd]
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL)

    def _get_next_export_id(self) -> int:
        """Retrieve the next available export ID, and update the rados key

        :returns: The export ID
        :rtype: str
        """
        next_id = int(self._rados_get(self.export_counter))
        file = self._tmpfile(next_id + 1)
        self._rados_put(self.export_counter, file.name)
        return next_id

    def _tmpfile(self, value: str) -> tempfile._TemporaryFileWrapper:
        file = tempfile.NamedTemporaryFile(mode='w+')
        file.write(str(value))
        file.seek(0)
        return file

    def _rados_get(self, name: str) -> str:
        """Retrieve the content of the RADOS object with a given name

        :param name: Name of the RADOS object to retrieve

        :returns: Contents of the RADOS object
        :rtype: str
        """
        cmd = [
            'rados', '-p', self.ceph_pool, '--id', self.client_name,
            'get', name, '/dev/stdout'
        ]
        logging.debug("About to call: {}".format(cmd))
        output = subprocess.check_output(cmd)
        return output.decode('utf-8')

    def _rados_put(self, name: str, source: str):
        """Store the contents of the source file in a named RADOS object.

        :param name: Name of the RADOS object to retrieve
        :param source: Path to a file to upload to RADOS.

        :returns: None
        """
        cmd = [
            'rados', '-p', self.ceph_pool, '--id', self.client_name,
            'put', name, source
        ]
        logging.debug("About to call: {}".format(cmd))
        subprocess.check_call(cmd)

    def _rados_rm(self, name: str):
        """Remove a named RADOS object.

        :param name: Name of the RADOS object to remove
        :param source: Path to a file to upload to RADOS.

        :returns: None
        """
        cmd = [
            'rados', '-p', self.ceph_pool, '--id', self.client_name,
            'rm', name
        ]
        logging.debug("About to call: {}".format(cmd))
        subprocess.check_call(cmd)

    def _add_share_to_index(self, export_id: int):
        """Add an export RADOS object's URL to the RADOS URL index."""
        index_data = self._rados_get(self.export_index)
        url = '%url rados://{}/ganesha-export-{}'.format(
            self.ceph_pool, export_id
        )
        rados_urls = index_data.split('\n')
        if url not in rados_urls:
            rados_urls.append(url)
            tmpfile = self._tmpfile('\n'.join(rados_urls))
            self._rados_put(self.export_index, tmpfile.name)

    def _remove_share_from_index(self, export_id: int):
        """Remove an export RADOS object's URL from the RADOS URL index."""
        index_data = self._rados_get(self.export_index)
        if not index_data:
            return

        unwanted_url = "%url rados://{0}/{1}".format(
            self.ceph_pool,
            'ganesha-export-{}'.format(export_id))
        logging.debug("Looking for '{}' in index".format(unwanted_url))
        rados_urls = index_data.split('\n')
        logging.debug("Index URLs: {}".format(rados_urls))
        index = [url.strip() for url in rados_urls if url != unwanted_url]
        logging.debug("Index URLs without unwanted: {}".format(index))
        tmpfile = self._tmpfile('\n'.join(index))
        self._rados_put(self.export_index, tmpfile.name)
