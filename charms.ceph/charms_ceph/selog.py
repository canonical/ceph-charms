# Copyright 2026 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from datetime import datetime, timezone
import json

from charmhelpers.core.hookenv import log as ch_log


def _default_log_output(msg):
    ch_log(msg)


_log_callback = _default_log_output
_defaults = {}


def _merge_dicts(dst, src):
    for k, v in src.items():
        if k not in dst:
            dst[k] = v


def _make_log_str(description, level, msg, appid, event, detail):
    if not (event.startswith('sys_') or
            event.startswith('authn_') or
            event.startswith('authz_')):
        raise ValueError('event must start with one of sys, authn or authz')

    level = level.upper()
    if level not in ('INFO', 'WARN', 'ERROR'):
        raise ValueError('level must be one of INFO, WARN, ERROR')

    now_utc = datetime.now(timezone.utc).isoformat()
    obj = {'level': level, 'msg': msg, 'type': 'security',
           'datetime': now_utc, 'appid': appid,
           'event': event, 'detail': detail, 'description': description}
    return json.dumps(obj)


def log(description, level='WARN', **kwargs):
    msg = kwargs.get('msg', None)
    if msg is None:
        kwargs['msg'] = description

    kwargs.setdefault('detail', '')
    _merge_dicts(kwargs, _defaults)
    return _log_callback(_make_log_str(description, level, **kwargs))


def register_log_callback(fn):
    global _log_callback
    ret = _log_callback
    _log_callback = fn
    return ret


def register_defaults(dfls):
    global _defaults
    prev = _defaults
    _defaults = dfls
    return prev
