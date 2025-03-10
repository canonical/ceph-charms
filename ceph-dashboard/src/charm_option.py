#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

import charmhelpers.core.host as ch_host
from typing import List, Union


class CharmCephOption():
    """Manage a charm option to ceph command to manage that option"""

    def __init__(
        self, charm_option_name, ceph_option_name, min_version=None
    ):
        self.charm_option_name = charm_option_name
        self.ceph_option_name = ceph_option_name
        self.min_version = min_version

    def is_supported(self) -> bool:
        """Is the option supported on this unit"""
        if self.min_version:
            return self.minimum_supported(self.min_version)
        return True

    def minimum_supported(self, supported_version: str) -> bool:
        """Check if installed Ceph release is >= to supported_version"""
        return ch_host.cmp_pkgrevno('ceph-common', supported_version) >= 0

    def convert_option(self, value: Union[bool, str, int]) -> List[str]:
        """Convert a value to the corresponding value part of the ceph
            dashboard command"""
        return [str(value)]

    def ceph_command(self, value: List[str]) -> List[str]:
        """Shell command to set option to desired value"""
        cmd = ['ceph', 'dashboard', self.ceph_option_name]
        cmd.extend(self.convert_option(value))
        return cmd


class DebugOption(CharmCephOption):

    def convert_option(self, value):
        """Convert charm True/False to enable/disable"""
        if value:
            return ['enable']
        else:
            return ['disable']


class MOTDOption(CharmCephOption):

    def convert_option(self, value):
        """Split motd charm option into ['severity', 'time', 'message']"""
        if value:
            return value.split('|')
        else:
            return ['clear']


class CharmCephOptionList():
    def get(self) -> List:
        """Get Charm options list"""
        return [
            DebugOption('debug', 'debug'),
            CharmCephOption(
                'enable-password-policy',
                'set-pwd-policy-enabled'),
            CharmCephOption(
                'password-policy-check-length',
                'set-pwd-policy-check-length-enabled'),
            CharmCephOption(
                'password-policy-check-oldpwd',
                'set-pwd-policy-check-oldpwd-enabled'),
            CharmCephOption(
                'password-policy-check-username',
                'set-pwd-policy-check-username-enabled'),
            CharmCephOption(
                'password-policy-check-exclusion-list',
                'set-pwd-policy-check-exclusion-list-enabled'),
            CharmCephOption(
                'password-policy-check-complexity',
                'set-pwd-policy-check-complexity-enabled'),
            CharmCephOption(
                'password-policy-check-sequential-chars',
                'set-pwd-policy-check-sequential-chars-enabled'),
            CharmCephOption(
                'password-policy-check-repetitive-chars',
                'set-pwd-policy-check-repetitive-chars-enabled'),
            CharmCephOption(
                'password-policy-min-length',
                'set-pwd-policy-min-length'),
            CharmCephOption(
                'password-policy-min-complexity',
                'set-pwd-policy-min-complexity'),
            CharmCephOption(
                'audit-api-enabled',
                'set-audit-api-enabled'),
            CharmCephOption(
                'audit-api-log-payload',
                'set-audit-api-log-payload'),
            MOTDOption(
                'motd',
                'motd',
                min_version='15.2.14')
        ]
