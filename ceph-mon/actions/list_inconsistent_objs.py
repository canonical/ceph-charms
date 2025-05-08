#!/usr/bin/env python3
#
# Copyright 2021 Canonical Ltd
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

import json
import re
import sys
from subprocess import check_output, CalledProcessError
import yaml

sys.path.append('hooks')

from charmhelpers.core.hookenv import function_fail, function_get, \
    function_set, log


VALID_FORMATS = ('text', 'json', 'yaml')


def get_health_detail():
    return check_output(['ceph', 'health', 'detail']).decode('UTF-8')


def get_rados_inconsistent(pg):
    return check_output(['rados', 'list-inconsistent-obj', pg]).decode('UTF-8')


def get_inconsistent_objs():
    # For the call to 'ceph health detail' we are interested in
    # lines with the form:
    # pg $PG is ...inconsistent...
    rx = re.compile('pg (\\S+) .+inconsistent')
    out = get_health_detail()
    msg = {}   # Maps PG -> object name list.

    for line in out.split('\n'):
        res = rx.search(line)
        if res is None:
            continue

        pg = res.groups()[0]
        out = get_rados_inconsistent(pg)
        js = json.loads(out)
        inconsistents = js.get('inconsistents')

        if not inconsistents:
            continue

        msg.setdefault(pg, []).extend(x['object']['name']
                                      for x in inconsistents)

    return msg


def text_format(obj):
    ret = ''
    for pg, objs in obj.items():
        ret += '{}: {}'.format(pg, ','.join(objs))
    return ret


if __name__ == '__main__':
    try:
        fmt = function_get('format')
        if fmt and fmt not in VALID_FORMATS:
            function_fail('Unknown format specified: {}'.format(fmt))
        else:
            msg = get_inconsistent_objs()
            if fmt == 'yaml':
                msg = yaml.dump(msg)
            elif fmt == 'json':
                msg = json.dumps(msg, indent=4, sort_keys=True)
            else:
                msg = text_format(msg)
            function_set({'message': msg})
    except CalledProcessError as e:
        log(e)
        function_fail("Listing inconsistent objects failed with error {}"
                      .format(str(e)))
