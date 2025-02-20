# Copyright 2018 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Ceph Testing."""

import unittest
import json
import logging
import requests
import boto3
import botocore.exceptions
import urllib3

import tenacity

import zaza.openstack.charm_tests.test_utils as test_utils
import zaza.model as zaza_model
import zaza.openstack.utilities.ceph as zaza_ceph
import zaza.openstack.utilities.generic as zaza_utils
import zaza.utilities.juju as juju_utils
import zaza.openstack.utilities.openstack as zaza_openstack
import zaza.openstack.utilities.generic as generic_utils

# Disable warnings for ssl_verify=false
urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)


class CephRGWTest(test_utils.BaseCharmTest):
    """Ceph RADOS Gateway Daemons Test Class.

    This Testset is not idempotent, because we don't support scale down from
    multisite to singlesite (yet). Tests can be performed independently.
    However, If test_100 has completed migration, retriggering the test-set
    would cause a time-out in test_003.
    """

    # String Resources
    primary_rgw_app = 'ceph-radosgw'
    primary_rgw_unit = 'ceph-radosgw/0'
    secondary_rgw_app = 'secondary-ceph-radosgw'
    secondary_rgw_unit = 'secondary-ceph-radosgw/0'

    @classmethod
    def setUpClass(cls):
        """Run class setup for running ceph low level tests."""
        super(CephRGWTest, cls).setUpClass(application_name='ceph-radosgw')

    @property
    def expected_apps(self):
        """Determine application names for ceph-radosgw apps."""
        _apps = [
            self.primary_rgw_app
        ]
        try:
            zaza_model.get_application(self.secondary_rgw_app)
            _apps.append(self.secondary_rgw_app)
        except KeyError:
            pass
        return _apps

    @property
    def multisite(self):
        """Determine whether deployment is multi-site."""
        try:
            zaza_model.get_application(self.secondary_rgw_app)
            return True
        except KeyError:
            return False

    def get_rgwadmin_cmd_skeleton(self, unit_name):
        """
        Get radosgw-admin cmd skeleton with rgw.hostname populated key.

        :param unit_name: Unit on which the complete command would be run.
        :type unit_name: str
        :returns: hostname filled basic command skeleton
        :rtype: str
        """
        app_name = unit_name.split('/')[0]
        juju_units = zaza_model.get_units(app_name)
        unit_hostnames = generic_utils.get_unit_hostnames(juju_units)
        hostname = unit_hostnames[unit_name]
        return 'radosgw-admin --id=rgw.{} '.format(hostname)

    def purge_bucket(self, application, bucket_name):
        """Remove a bucket and all it's objects.

        :param application: RGW application name
        :type application: str
        :param bucket_name: Name for RGW bucket to be deleted
        :type bucket_name: str
        """
        juju_units = zaza_model.get_units(application)
        unit_hostnames = generic_utils.get_unit_hostnames(juju_units)
        for unit_name, hostname in unit_hostnames.items():
            key_name = "rgw.{}".format(hostname)
            cmd = 'radosgw-admin --id={} bucket rm --bucket={}' \
                  ' --purge-objects'.format(key_name, bucket_name)
            zaza_model.run_on_unit(unit_name, cmd)

    def wait_for_status(self, application,
                        is_primary=False, sync_expected=True):
        """Wait for required RGW endpoint to finish sync for data and metadata.

        :param application: RGW application which has to be waited for
        :type application: str
        :param is_primary: whether RGW application is primary or secondary
        :type is_primary: boolean
        :param sync_expected: whether sync details should be expected in status
        :type sync_expected: boolean
        """
        juju_units = zaza_model.get_units(application)
        unit_hostnames = generic_utils.get_unit_hostnames(juju_units)
        data_check = 'data is caught up with source'
        meta_primary = 'metadata sync no sync (zone is master)'
        meta_secondary = 'metadata is caught up with master'
        meta_check = meta_primary if is_primary else meta_secondary

        for attempt in tenacity.Retrying(
            wait=tenacity.wait_exponential(multiplier=10, max=300),
            reraise=True, stop=tenacity.stop_after_attempt(12),
            retry=tenacity.retry_if_exception_type(AssertionError)
        ):
            with attempt:
                for unit_name, hostname in unit_hostnames.items():
                    key_name = "rgw.{}".format(hostname)
                    cmd = 'radosgw-admin --id={} sync status'.format(key_name)
                    stdout = zaza_model.run_on_unit(
                        unit_name, cmd
                    ).get('Stdout', '')
                    if sync_expected:
                        # Both data and meta sync.
                        self.assertIn(data_check, stdout)
                        self.assertIn(meta_check, stdout)
                    else:
                        #  ExpectPrimary's Meta Status and no Data sync status
                        self.assertIn(meta_primary, stdout)
                        self.assertNotIn(data_check, stdout)

    def fetch_rgw_object(self, target_client, container_name, object_name):
        """Fetch RGW object content.

        :param target_client: boto3 client object configured for an endpoint.
        :type target_client: str
        :param container_name: RGW bucket name for desired object.
        :type container_name: str
        :param object_name: Object name for desired object.
        :type object_name: str
        """
        for attempt in tenacity.Retrying(
            wait=tenacity.wait_exponential(multiplier=1, max=60),
            reraise=True, stop=tenacity.stop_after_attempt(12)
        ):
            with attempt:
                return target_client.Object(
                    container_name, object_name
                ).get()['Body'].read().decode('UTF-8')

    def promote_rgw_to_primary(self, app_name: str):
        """Promote provided app to Primary and update period at new secondary.

        :param app_name: Secondary site rgw Application to be promoted.
        :type app_name: str
        """
        if app_name is self.primary_rgw_app:
            new_secondary = self.secondary_rgw_unit
        else:
            new_secondary = self.primary_rgw_unit

        # Promote to Primary
        zaza_model.run_action_on_leader(
            app_name,
            'promote',
            action_params={},
        )

        # Period Update Commit new secondary.
        cmd = self.get_rgwadmin_cmd_skeleton(new_secondary)
        zaza_model.run_on_unit(
            new_secondary, cmd + 'period update --commit'
        )

    def get_client_keys(self, rgw_app_name=None):
        """Create access_key and secret_key for boto3 client.

        :param rgw_app_name: RGW application for which keys are required.
        :type rgw_app_name: str
        """
        unit_name = self.primary_rgw_unit
        if rgw_app_name is not None:
            unit_name = rgw_app_name + '/0'
        user_name = 'botoclient'
        cmd = self.get_rgwadmin_cmd_skeleton(unit_name)
        users = json.loads(zaza_model.run_on_unit(
            unit_name, cmd + 'user list'
        ).get('Stdout', ''))
        # Fetch boto3 user keys if user exists.
        if user_name in users:
            output = json.loads(zaza_model.run_on_unit(
                unit_name, cmd + 'user info --uid={}'.format(user_name)
            ).get('Stdout', ''))
            keys = output['keys'][0]
            return keys['access_key'], keys['secret_key']
        # Create boto3 user if it does not exist.
        create_cmd = cmd + 'user create --uid={} --display-name={}'.format(
            user_name, user_name
        )
        output = json.loads(
            zaza_model.run_on_unit(unit_name, create_cmd).get('Stdout', '')
        )
        keys = output['keys'][0]
        return keys['access_key'], keys['secret_key']

    @tenacity.retry(
        retry=tenacity.retry_if_result(lambda ret: ret is None),
        wait=tenacity.wait_fixed(10),
        stop=tenacity.stop_after_attempt(5)
    )
    def get_rgw_endpoint(self, unit_name: str):
        """Fetch Application endpoint for RGW unit.

        :param unit_name: Unit name for which RGW endpoint is required.
        :type unit_name: str
        """
        # Get address  "public" network binding.
        unit_address = zaza_model.run_on_unit(
            unit_name, "network-get public --bind-address"
        ).get('Stdout', '').strip()

        logging.info("Unit: {}, Endpoint: {}".format(unit_name, unit_address))
        if unit_address is None:
            return None
        # Evaluate port
        try:
            zaza_model.get_application("vault")
            return "https://{}:443".format(unit_address)
        except KeyError:
            return "http://{}:80".format(unit_address)

    def configure_rgw_apps_for_multisite(self):
        """Configure Multisite values on primary and secondary apps."""
        realm = 'zaza_realm'
        zonegroup = 'zaza_zg'

        zaza_model.set_application_config(
            self.primary_rgw_app,
            {
                'realm': realm,
                'zonegroup': zonegroup,
                'zone': 'zaza_primary'
            }
        )
        zaza_model.set_application_config(
            self.secondary_rgw_app,
            {
                'realm': realm,
                'zonegroup': zonegroup,
                'zone': 'zaza_secondary'
            }
        )

    def configure_rgw_multisite_relation(self):
        """Configure multi-site relation between primary and secondary apps."""
        multisite_relation = zaza_model.get_relation_id(
            self.primary_rgw_app, self.secondary_rgw_app,
            remote_interface_name='secondary'
        )
        if multisite_relation is None:
            logging.info('Configuring Multisite')
            self.configure_rgw_apps_for_multisite()
            zaza_model.add_relation(
                self.primary_rgw_app,
                self.primary_rgw_app + ":primary",
                self.secondary_rgw_app + ":secondary"
            )
            zaza_model.block_until_unit_wl_status(
                self.secondary_rgw_unit, "waiting"
            )

        zaza_model.block_until_unit_wl_status(
            self.secondary_rgw_unit, "active"
        )
        zaza_model.block_until_unit_wl_status(
            self.primary_rgw_unit, "active"
        )
        zaza_model.wait_for_unit_idle(self.secondary_rgw_unit)
        zaza_model.wait_for_unit_idle(self.primary_rgw_unit)

    def clean_rgw_multisite_config(self, app_name):
        """Clear Multisite Juju config values to default.

        :param app_name: App for which config values are to be cleared
        :type app_name: str
        """
        unit_name = app_name + "/0"
        zaza_model.set_application_config(
            app_name,
            {
                'realm': "",
                'zonegroup': "",
                'zone': "default"
            }
        )
        # Commit changes to period.
        cmd = self.get_rgwadmin_cmd_skeleton(unit_name)
        zaza_model.run_on_unit(
            unit_name, cmd + 'period update --commit --rgw-zone=default '
            '--rgw-zonegroup=default'
        )

    def enable_virtual_hosted_bucket(self):
        """Enable virtual hosted bucket on primary rgw app."""
        zaza_model.set_application_config(
            self.primary_rgw_app,
            {
                'virtual-hosted-bucket-enabled': "true"
            }
        )

    def set_os_public_hostname(self):
        """Set os-public-hostname on primary rgw app."""
        zaza_model.set_application_config(
            self.primary_rgw_app,
            {
                'os-public-hostname': "rgw.example.com",
            }
        )

    def clean_virtual_hosted_bucket(self):
        """Clear virtual hosted bucket on primary app."""
        zaza_model.set_application_config(
            self.primary_rgw_app,
            {
                'os-public-hostname': "",
                'virtual-hosted-bucket-enabled': "false"
            }
        )

    def test_001_processes(self):
        """Verify Ceph processes.

        Verify that the expected service processes are running
        on each ceph unit.
        """
        logging.info('Checking radosgw processes...')
        # Process name and quantity of processes to expect on each unit
        ceph_radosgw_processes = {
            'radosgw': 1,
        }

        # Units with process names and PID quantities expected
        expected_processes = {}
        for app in self.expected_apps:
            for unit in zaza_model.get_units(app):
                expected_processes[unit.entity_id] = ceph_radosgw_processes

        actual_pids = zaza_utils.get_unit_process_ids(expected_processes)
        ret = zaza_utils.validate_unit_process_ids(expected_processes,
                                                   actual_pids)
        self.assertTrue(ret)

    def test_002_services(self):
        """Verify the ceph services.

        Verify the expected services are running on the service units.
        """
        logging.info('Checking radosgw services...')
        services = ['radosgw', 'haproxy']
        for app in self.expected_apps:
            for unit in zaza_model.get_units(app):
                zaza_model.block_until_service_status(
                    unit_name=unit.entity_id,
                    services=services,
                    target_status='running'
                )

    def test_003_object_storage_and_secondary_block(self):
        """Verify Object Storage API and Secondary Migration block."""
        container_name = 'zaza-container'
        obj_data = 'Test data from Zaza'
        obj_name = 'prefile'

        logging.info('Checking Object Storage API for Primary Cluster')
        # 1. Fetch Primary Endpoint Details
        primary_endpoint = self.get_rgw_endpoint(self.primary_rgw_unit)
        self.assertNotEqual(primary_endpoint, None)

        # 2. Create RGW Client and perform IO
        access_key, secret_key = self.get_client_keys()
        primary_client = boto3.resource("s3",
                                        verify=False,
                                        endpoint_url=primary_endpoint,
                                        aws_access_key_id=access_key,
                                        aws_secret_access_key=secret_key)
        primary_client.Bucket(container_name).create()
        primary_object_one = primary_client.Object(
            container_name,
            obj_name
        )
        primary_object_one.put(Body=obj_data)

        # 3. Fetch Object and Perform Data Integrity check.
        content = primary_object_one.get()['Body'].read().decode('UTF-8')
        self.assertEqual(content, obj_data)

        # Skip multisite tests if not compatible with bundle.
        if not self.multisite:
            logging.info('Skipping Secondary Object gatewaty verification')
            return

        logging.info('Checking Object Storage API for Secondary Cluster')
        # 1. Fetch Secondary Endpoint Details
        secondary_endpoint = self.get_rgw_endpoint(self.secondary_rgw_unit)
        self.assertNotEqual(secondary_endpoint, None)

        # 2. Create RGW Client and perform IO
        access_key, secret_key = self.get_client_keys(self.secondary_rgw_app)
        secondary_client = boto3.resource("s3",
                                          verify=False,
                                          endpoint_url=secondary_endpoint,
                                          aws_access_key_id=access_key,
                                          aws_secret_access_key=secret_key)
        secondary_client.Bucket(container_name).create()
        secondary_object = secondary_client.Object(
            container_name,
            obj_name
        )
        secondary_object.put(Body=obj_data)

        # 3. Fetch Object and Perform Data Integrity check.
        content = secondary_object.get()['Body'].read().decode('UTF-8')
        self.assertEqual(content, obj_data)

        logging.info('Checking Secondary Migration Block')
        # 1. Migrate to multisite
        if zaza_model.get_relation_id(
                self.primary_rgw_app, self.secondary_rgw_app,
                remote_interface_name='secondary'
        ) is not None:
            logging.info('Skipping Test, Multisite relation already present.')
            return

        logging.info('Configuring Multisite')
        self.configure_rgw_apps_for_multisite()
        zaza_model.add_relation(
            self.primary_rgw_app,
            self.primary_rgw_app + ":primary",
            self.secondary_rgw_app + ":secondary"
        )

        # 2. Verify secondary fails migration due to existing Bucket.
        assert_state = {
            self.secondary_rgw_app: {
                "workload-status": "blocked",
                "workload-status-message-prefix":
                    "Non-Pristine RGW site can't be used as secondary"
            }
        }
        zaza_model.wait_for_application_states(states=assert_state,
                                               timeout=900)

        # 3. Perform Secondary Cleanup
        logging.info('Perform cleanup at secondary')
        self.clean_rgw_multisite_config(self.secondary_rgw_app)
        zaza_model.remove_relation(
            self.primary_rgw_app,
            self.primary_rgw_app + ":primary",
            self.secondary_rgw_app + ":secondary"
        )

        # Make secondary pristine.
        self.purge_bucket(self.secondary_rgw_app, container_name)

        zaza_model.block_until_unit_wl_status(self.secondary_rgw_unit,
                                              'active')

    def test_004_multisite_directional_sync_policy(self):
        """Verify Multisite Directional Sync Policy."""
        # Skip multisite tests if not compatible with bundle.
        if not self.multisite:
            logging.info('Skipping multisite sync policy verification')
            return

        container_name = 'zaza-container'
        primary_obj_name = 'primary-testfile'
        primary_obj_data = 'Primary test data'
        secondary_directional_obj_name = 'secondary-directional-testfile'
        secondary_directional_obj_data = 'Secondary directional test data'
        secondary_symmetrical_obj_name = 'secondary-symmetrical-testfile'
        secondary_symmetrical_obj_data = 'Secondary symmetrical test data'

        logging.info('Verifying multisite directional sync policy')

        # Set default sync policy to "allowed", which allows buckets to sync,
        # but the sync is disabled by default in the zone group. Also, set the
        # secondary zone sync policy flow type policy to "directional".
        zaza_model.set_application_config(
            self.primary_rgw_app,
            {
                "sync-policy-state": "allowed",
            }
        )
        zaza_model.set_application_config(
            self.secondary_rgw_app,
            {
                "sync-policy-flow-type": "directional",
            }
        )
        zaza_model.wait_for_unit_idle(self.secondary_rgw_unit)
        zaza_model.wait_for_unit_idle(self.primary_rgw_unit)

        # Setup multisite relation.
        self.configure_rgw_multisite_relation()

        logging.info('Waiting for Data and Metadata to Synchronize')
        # NOTE: We only check the secondary zone, because the sync policy flow
        # type is set to "directional" between the primary and secondary zones.
        self.wait_for_status(self.secondary_rgw_app, is_primary=False)

        # Create bucket on primary RGW zone.
        logging.info('Creating bucket on primary zone')
        primary_endpoint = self.get_rgw_endpoint(self.primary_rgw_unit)
        self.assertNotEqual(primary_endpoint, None)

        access_key, secret_key = self.get_client_keys()
        primary_client = boto3.resource("s3",
                                        verify=False,
                                        endpoint_url=primary_endpoint,
                                        aws_access_key_id=access_key,
                                        aws_secret_access_key=secret_key)
        primary_client.Bucket(container_name).create()

        # Enable sync on the bucket.
        logging.info('Enabling sync on the bucket from the primary zone')
        zaza_model.run_action_on_leader(
            self.primary_rgw_app,
            'enable-buckets-sync',
            action_params={
                'buckets': container_name,
            },
            raise_on_failure=True,
        )

        # Check that sync cannot be enabled using secondary Juju RGW app.
        with self.assertRaises(zaza_model.ActionFailed):
            zaza_model.run_action_on_leader(
                self.secondary_rgw_app,
                'enable-buckets-sync',
                action_params={
                    'buckets': container_name,
                },
                raise_on_failure=True,
            )

        logging.info('Waiting for Data and Metadata to Synchronize')
        self.wait_for_status(self.secondary_rgw_app, is_primary=False)

        # Perform IO on primary zone bucket.
        logging.info('Performing IO on primary zone bucket')
        primary_object = primary_client.Object(
            container_name,
            primary_obj_name
        )
        primary_object.put(Body=primary_obj_data)

        # Verify that the object is replicated to the secondary zone.
        logging.info('Verifying that the object is replicated to the '
                     'secondary zone')
        secondary_endpoint = self.get_rgw_endpoint(self.secondary_rgw_unit)
        self.assertNotEqual(secondary_endpoint, None)

        secondary_client = boto3.resource("s3",
                                          verify=False,
                                          endpoint_url=secondary_endpoint,
                                          aws_access_key_id=access_key,
                                          aws_secret_access_key=secret_key)
        secondary_data = self.fetch_rgw_object(
            secondary_client,
            container_name,
            primary_obj_name
        )
        self.assertEqual(secondary_data, primary_obj_data)

        # Write object to the secondary zone bucket, when the sync policy
        # flow type is set to "directional" between the zones.
        logging.info('Writing object to the secondary zone bucket, which '
                     'should not be replicated to the primary zone')
        secondary_object = secondary_client.Object(
            container_name,
            secondary_directional_obj_name
        )
        secondary_object.put(Body=secondary_directional_obj_data)

        # Verify that the object is not replicated to the primary zone.
        logging.info('Verifying that the object is not replicated to the '
                     'primary zone')
        with self.assertRaises(botocore.exceptions.ClientError):
            self.fetch_rgw_object(
                primary_client,
                container_name,
                secondary_directional_obj_name
            )

        logging.info('Setting sync policy flow to "symmetrical" on the '
                     'secondary RGW zone')
        zaza_model.set_application_config(
            self.secondary_rgw_app,
            {
                "sync-policy-flow-type": "symmetrical",
            }
        )
        zaza_model.wait_for_unit_idle(self.secondary_rgw_unit)
        zaza_model.wait_for_unit_idle(self.primary_rgw_unit)

        # Write another object to the secondary zone bucket.
        logging.info('Writing another object to the secondary zone bucket.')
        secondary_object = secondary_client.Object(
            container_name,
            secondary_symmetrical_obj_name
        )
        secondary_object.put(Body=secondary_symmetrical_obj_data)

        logging.info('Waiting for Data and Metadata to Synchronize')
        # NOTE: This time, we check both the primary and secondary zones,
        # because the sync policy flow type is set to "symmetrical" between
        # the zones.
        self.wait_for_status(self.secondary_rgw_app, is_primary=False)
        self.wait_for_status(self.primary_rgw_app, is_primary=True)

        # Verify that all objects are replicated to the primary zone.
        logging.info('Verifying that all objects are replicated to the '
                     'primary zone (including older objects).')
        test_cases = [
            {
                'obj_name': primary_obj_name,
                'obj_data': primary_obj_data,
            },
            {
                'obj_name': secondary_directional_obj_name,
                'obj_data': secondary_directional_obj_data,
            },
            {
                'obj_name': secondary_symmetrical_obj_name,
                'obj_data': secondary_symmetrical_obj_data,
            },
        ]
        for tc in test_cases:
            logging.info('Verifying that object "{}" is replicated'.format(
                tc['obj_name']))
            primary_data = self.fetch_rgw_object(
                primary_client,
                container_name,
                tc['obj_name']
            )
            self.assertEqual(primary_data, tc['obj_data'])

        # Cleanup.
        logging.info('Cleaning up buckets after test case')
        self.purge_bucket(self.primary_rgw_app, container_name)
        self.purge_bucket(self.secondary_rgw_app, container_name)

        logging.info('Waiting for Data and Metadata to Synchronize')
        self.wait_for_status(self.secondary_rgw_app, is_primary=False)
        self.wait_for_status(self.primary_rgw_app, is_primary=True)

        # Set multisite sync policy state to "enabled" on the primary RGW app.
        # Paired with "symmetrical" sync policy flow on the secondary RGW app,
        # this enables bidirectional sync between the zones (which is the
        # default behaviour without multisite sync policies configured).
        logging.info('Setting sync policy state to "enabled".')
        zaza_model.set_application_config(
            self.primary_rgw_app,
            {
                "sync-policy-state": "enabled",
            }
        )
        zaza_model.wait_for_unit_idle(self.primary_rgw_unit)

    def test_100_migration_and_multisite_failover(self):
        """Perform multisite migration and verify failover."""
        container_name = 'zaza-container'
        obj_data = 'Test data from Zaza'
        # Skip multisite tests if not compatible with bundle.
        if not self.multisite:
            raise unittest.SkipTest('Skipping Migration Test')

        logging.info('Perform Pre-Migration IO')
        # 1. Fetch Endpoint Details
        primary_endpoint = self.get_rgw_endpoint(self.primary_rgw_unit)
        self.assertNotEqual(primary_endpoint, None)

        # 2. Create primary client and add pre-migration object.
        access_key, secret_key = self.get_client_keys()
        primary_client = boto3.resource("s3",
                                        verify=False,
                                        endpoint_url=primary_endpoint,
                                        aws_access_key_id=access_key,
                                        aws_secret_access_key=secret_key)
        primary_client.Bucket(container_name).create()
        primary_client.Object(
            container_name,
            'prefile'
        ).put(Body=obj_data)

        # If Primary/Secondary relation does not exist, add it.
        self.configure_rgw_multisite_relation()

        logging.info('Waiting for Data and Metadata to Synchronize')
        self.wait_for_status(self.secondary_rgw_app, is_primary=False)
        self.wait_for_status(self.primary_rgw_app, is_primary=True)

        logging.info('Performing post migration IO tests.')
        # Add another object at primary
        primary_client.Object(
            container_name,
            'postfile'
        ).put(Body=obj_data)

        # 1. Fetch Endpoint Details
        secondary_endpoint = self.get_rgw_endpoint(self.secondary_rgw_unit)
        self.assertNotEqual(secondary_endpoint, None)

        # 2. Create secondary client and fetch synchronised objects.
        secondary_client = boto3.resource("s3",
                                          verify=False,
                                          endpoint_url=secondary_endpoint,
                                          aws_access_key_id=access_key,
                                          aws_secret_access_key=secret_key)

        # 3. Verify Data Integrity
        # fetch_rgw_object has internal retry so waiting for sync beforehand
        # is not required for post migration object sync.
        pre_migration_data = self.fetch_rgw_object(
            secondary_client, container_name, 'prefile'
        )
        post_migration_data = self.fetch_rgw_object(
            secondary_client, container_name, 'postfile'
        )

        # 4. Verify Syncronisation works and objects are replicated
        self.assertEqual(pre_migration_data, obj_data)
        self.assertEqual(post_migration_data, obj_data)

        logging.info('Checking multisite failover/failback')
        # Failover Scenario, Promote Secondary-Ceph-RadosGW to Primary
        self.promote_rgw_to_primary(self.secondary_rgw_app)

        # Wait for Sites to be syncronised.
        self.wait_for_status(self.primary_rgw_app, is_primary=False)
        self.wait_for_status(self.secondary_rgw_app, is_primary=True)

        # IO Test
        container = 'failover-container'
        test_data = 'Test data from Zaza on Secondary'
        secondary_client.Bucket(container).create()
        secondary_object = secondary_client.Object(container, 'testfile')
        secondary_object.put(
            Body=test_data
        )
        secondary_content = secondary_object.get()[
            'Body'
        ].read().decode('UTF-8')

        # Wait for Sites to be syncronised.
        self.wait_for_status(self.primary_rgw_app, is_primary=False)
        self.wait_for_status(self.secondary_rgw_app, is_primary=True)

        # Recovery scenario, reset ceph-rgw as primary.
        self.promote_rgw_to_primary(self.primary_rgw_app)
        self.wait_for_status(self.primary_rgw_app, is_primary=True)
        self.wait_for_status(self.secondary_rgw_app, is_primary=False)

        # Fetch Syncronised copy of testfile from primary site.
        primary_content = self.fetch_rgw_object(
            primary_client, container, 'testfile'
        )

        # Verify Data Integrity.
        self.assertEqual(secondary_content, primary_content)

        # Scaledown and verify replication has stopped.
        logging.info('Checking multisite scaledown')
        zaza_model.remove_relation(
            self.primary_rgw_app,
            self.primary_rgw_app + ":primary",
            self.secondary_rgw_app + ":secondary"
        )

        # wait for sync stop
        self.wait_for_status(self.primary_rgw_app, sync_expected=False)
        self.wait_for_status(self.secondary_rgw_app, sync_expected=False)

        # Refresh client and verify objects are not replicating.
        primary_client = boto3.resource("s3",
                                        verify=False,
                                        endpoint_url=primary_endpoint,
                                        aws_access_key_id=access_key,
                                        aws_secret_access_key=secret_key)
        secondary_client = boto3.resource("s3",
                                          verify=False,
                                          endpoint_url=secondary_endpoint,
                                          aws_access_key_id=access_key,
                                          aws_secret_access_key=secret_key)

        # IO Test
        container = 'scaledown-container'
        test_data = 'Scaledown Test data'
        secondary_client.Bucket(container).create()
        secondary_object = secondary_client.Object(container, 'scaledown')
        secondary_object.put(
            Body=test_data
        )

        # Since bucket is not replicated.
        with self.assertRaises(botocore.exceptions.ClientError):
            primary_content = self.fetch_rgw_object(
                primary_client, container, 'scaledown'
            )

        # Cleanup of scaledown resources and synced resources.
        self.purge_bucket(self.secondary_rgw_app, container)
        self.purge_bucket(self.secondary_rgw_app, 'zaza-container')
        self.purge_bucket(self.secondary_rgw_app, 'failover-container')

    def test_101_virtual_hosted_bucket(self):
        """Test virtual hosted bucket."""
        # skip if quincy or older
        current_release = zaza_openstack.get_os_release(
            application='ceph-mon')
        reef = zaza_openstack.get_os_release('jammy_bobcat')
        if current_release < reef:
            raise unittest.SkipTest(
                'Virtual hosted bucket not supported in quincy or older')

        primary_rgw_unit = zaza_model.get_unit_from_name(self.primary_rgw_unit)
        if primary_rgw_unit.workload_status != "active":
            logging.info('Skipping virtual hosted bucket test since '
                         'primary rgw unit is not in active state')
            return

        logging.info('Testing virtual hosted bucket')

        # 0. Configure virtual hosted bucket
        self.enable_virtual_hosted_bucket()
        zaza_model.block_until_wl_status_info_starts_with(
            self.primary_rgw_app,
            'os-public-hostname must have a value',
            timeout=900
        )
        self.set_os_public_hostname()
        zaza_model.block_until_all_units_idle(self.model_name)
        container_name = 'zaza-bucket'
        obj_data = 'Test content from Zaza'
        obj_name = 'testfile'

        # 1. Fetch Primary Endpoint Details
        primary_endpoint = self.get_rgw_endpoint(self.primary_rgw_unit)
        self.assertNotEqual(primary_endpoint, None)

        # 2. Create RGW Client and perform IO
        access_key, secret_key = self.get_client_keys()
        primary_client = boto3.resource("s3",
                                        verify=False,
                                        endpoint_url=primary_endpoint,
                                        aws_access_key_id=access_key,
                                        aws_secret_access_key=secret_key)
        # We may not have certs for the pub hostname yet, so retry a few times.
        for attempt in tenacity.Retrying(
            stop=tenacity.stop_after_attempt(10),
            wait=tenacity.wait_fixed(4),
        ):
            with attempt:
                primary_client.Bucket(container_name).create()
        primary_object_one = primary_client.Object(
            container_name,
            obj_name
        )
        primary_object_one.put(Body=obj_data)
        primary_client.Bucket(container_name).Acl().put(ACL='public-read')
        primary_client.Object(container_name, obj_name).Acl().put(
            ACL='public-read'
        )

        # 3. Test if we can get content via virtual hosted bucket name
        public_hostname = zaza_model.get_application_config(
            self.primary_rgw_app
        )["os-public-hostname"]["value"]
        url = f"{primary_endpoint}/{obj_name}"
        headers = {'host': f"{container_name}.{public_hostname}"}
        f = requests.get(url, headers=headers, verify=False)
        self.assertEqual(f.text, obj_data)

        # 4. Cleanup and de-configure virtual hosted bucket
        self.clean_virtual_hosted_bucket()
        zaza_model.block_until_all_units_idle(self.model_name)
        self.purge_bucket(self.primary_rgw_app, container_name)


class BlueStoreCompressionCharmOperation(test_utils.BaseCharmTest):
    """Test charm handling of bluestore compression configuration options."""

    def _assert_pools_properties(self, pools, pools_detail,
                                 expected_properties, log_func=logging.info):
        """Check properties on a set of pools.

        :param pools: List of pool names to check.
        :type pools: List[str]
        :param pools_detail: List of dictionaries with pool detail
        :type pools_detail List[Dict[str,any]]
        :param expected_properties: Properties to check and their expected
                                    values.
        :type expected_properties: Dict[str,any]
        :returns: Nothing
        :raises: AssertionError
        """
        for pool in pools:
            for pd in pools_detail:
                if pd['pool_name'] == pool:
                    if 'options' in expected_properties:
                        for k, v in expected_properties['options'].items():
                            self.assertEqual(pd['options'][k], v)
                            log_func("['options']['{}'] == {}".format(k, v))
                    for k, v in expected_properties.items():
                        if k == 'options':
                            continue
                        self.assertEqual(pd[k], v)
                        log_func("{} == {}".format(k, v))

    def test_configure_compression(self):
        """Enable compression and validate properties flush through to pool."""
        # The Ceph RadosGW creates many light weight pools to keep track of
        # metadata, we only compress the pool containing actual data.
        app_pools = ['.rgw.buckets.data']

        ceph_pools_detail = zaza_ceph.get_ceph_pool_details(
            model_name=self.model_name)

        logging.debug('BEFORE: {}'.format(ceph_pools_detail))
        try:
            logging.info('Checking Ceph pool compression_mode prior to change')
            self._assert_pools_properties(
                app_pools, ceph_pools_detail,
                {'options': {'compression_mode': 'none'}})
        except KeyError:
            logging.info('property does not exist on pool, which is OK.')
        logging.info('Changing "bluestore-compression-mode" to "force" on {}'
                     .format(self.application_name))
        with self.config_change(
                {'bluestore-compression-mode': 'none'},
                {'bluestore-compression-mode': 'force'}):
            logging.info('Checking Ceph pool compression_mode after to change')
            self._check_pool_compression_mode(app_pools, 'force')

        logging.info('Checking Ceph pool compression_mode after '
                     'restoring config to previous value')
        self._check_pool_compression_mode(app_pools, 'none')

    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
        stop=tenacity.stop_after_attempt(10),
        reraise=True,
        retry=tenacity.retry_if_exception_type(AssertionError)
    )
    def _check_pool_compression_mode(self, app_pools, mode):
        ceph_pools_detail = zaza_ceph.get_ceph_pool_details(
            model_name=self.model_name)
        logging.debug('ceph_pools_details: %s', ceph_pools_detail)
        logging.debug(juju_utils.get_relation_from_unit(
            'ceph-mon', self.application_name, None,
            model_name=self.model_name))
        self._assert_pools_properties(
            app_pools, ceph_pools_detail,
            {'options': {'compression_mode': mode}})

    def test_invalid_compression_configuration(self):
        """Set invalid configuration and validate charm response."""
        stored_target_deploy_status = self.test_config.get(
            'target_deploy_status', {})
        new_target_deploy_status = stored_target_deploy_status.copy()
        new_target_deploy_status[self.application_name] = {
            'workload-status': 'blocked',
            'workload-status-message': 'Invalid configuration',
        }
        if 'target_deploy_status' in self.test_config:
            self.test_config['target_deploy_status'].update(
                new_target_deploy_status)
        else:
            self.test_config['target_deploy_status'] = new_target_deploy_status

        with self.config_change(
                {'bluestore-compression-mode': 'none'},
                {'bluestore-compression-mode': 'PEBCAK'}):
            logging.info('Charm went into blocked state as expected, restore '
                         'configuration')
            self.test_config[
                'target_deploy_status'] = stored_target_deploy_status


class CephKeyRotationTests(test_utils.BaseCharmTest):
    """Tests for the rotate-key action."""

    def _get_all_keys(self, unit, entity_filter):
        cmd = 'sudo ceph auth ls'
        result = zaza_model.run_on_unit(unit, cmd)
        # Don't use json formatting, as it's buggy upstream.
        data = result['Stdout'].split()
        ret = set()

        for ix, line in enumerate(data):
            # Structure:
            # $ENTITY
            # key:
            # key contents
            # That's why we need to move one position ahead.
            if 'key:' in line and entity_filter(data[ix - 1]):
                ret.add((data[ix - 1], data[ix + 1]))
        return ret

    def _check_key_rotation(self, entity, unit):
        def entity_filter(name):
            return name.startswith(entity)

        old_keys = self._get_all_keys(unit, entity_filter)
        action_obj = zaza_model.run_action(
            unit_name=unit,
            action_name='rotate-key',
            action_params={'entity': entity}
        )
        zaza_utils.assertActionRanOK(action_obj)
        # NOTE(lmlg): There's a nasty race going on here. Essentially,
        # since this action involves 2 different applications, what
        # happens is as follows:
        #          (1)            (2)               (3)              (4)
        # ceph-mon rotates key | (idle) | remote-unit rotates key | (idle)
        # Between (2) and (3), there's a window where all units are
        # idle, _but_ the key hasn't been rotated in the other unit.
        # As such, we retry a few times instead of using the
        # `wait_for_application_states` interface.

        for attempt in tenacity.Retrying(
            wait=tenacity.wait_exponential(multiplier=2, max=32),
            reraise=True, stop=tenacity.stop_after_attempt(20),
            retry=tenacity.retry_if_exception_type(AssertionError)
        ):
            with attempt:
                new_keys = self._get_all_keys(unit, entity_filter)
                self.assertNotEqual(old_keys, new_keys)

        diff = new_keys - old_keys
        self.assertEqual(len(diff), 1)
        first = next(iter(diff))
        # Check that the entity matches. The 'entity_filter'
        # callable will return a true-like value if it
        # matches the type of entity we're after (i.e: 'mgr')
        self.assertTrue(entity_filter(first[0]))

    def _get_rgw_client(self, unit):
        ret = self._get_all_keys(unit, lambda x: x.startswith('client.rgw'))
        if not ret:
            return None
        return next(iter(ret))[0]

    def test_key_rotate(self):
        """Test that rotating the keys actually changes them."""
        unit = 'ceph-mon/0'
        rgw_client = self._get_rgw_client(unit)

        if rgw_client:
            self._check_key_rotation(rgw_client, unit)
        else:
            logging.info('ceph-radosgw units present, but no RGW service')
