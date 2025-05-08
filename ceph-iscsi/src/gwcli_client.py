import logging
import subprocess

logger = logging.getLogger()


class GatewayClient():

    def run(self, path, cmd):
        _cmd = ['gwcli', path]
        # NOTE(lourot): we don't print the full command here as it might
        # contain secrets.
        logging.info(' '.join(_cmd) + ' ...')
        _cmd.extend(cmd.split())

        error_msg = None
        try:
            subprocess.check_output(_cmd, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            error_msg = 'gwcli failed with {}'.format(e.returncode)
            logging.error(error_msg)
            logging.error('stdout: {}'.format(e.stdout))
            logging.error('stderr: {}'.format(e.stderr))

        if error_msg:
            # NOTE(lourot): we re-raise another free-of-secrets exception:
            raise RuntimeError(error_msg)

    def create_target(self, iqn):
        self.run(
            "/iscsi-targets/",
            "create {}".format(iqn))

    def add_gateway_to_target(self, iqn, gateway_ip, gateway_fqdn):
        self.run(
            "/iscsi-targets/{}/gateways/".format(iqn),
            "create {} {}".format(gateway_fqdn, gateway_ip))

    def create_pool(self, pool_name, image_name, image_size):
        self.run(
            "/disks",
            "create pool={} image={} size={}".format(
                pool_name,
                image_name,
                image_size))

    def add_client_to_target(self, iqn, initiatorname):
        self.run(
            "/iscsi-targets/{}/hosts/".format(iqn),
            "create {}".format(initiatorname))

    def add_client_auth(self, iqn, initiatorname, username, password):
        self.run(
            "/iscsi-targets/{}/hosts/{}".format(iqn, initiatorname),
            "auth username={} password={}".format(username, password))

    def add_disk_to_client(self, iqn, initiatorname, pool_name, image_name):
        self.run(
            "/iscsi-targets/{}/hosts/{}".format(iqn, initiatorname),
            "disk add {}/{}".format(pool_name, image_name))
