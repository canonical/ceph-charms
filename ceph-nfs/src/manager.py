# Copyright (c) 2014 Red Hat, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# The contents of this file were copied, almost straight, from
# https://github.com/openstack/manila/blob/a3aaea91494665a25bdccebf69d9e85e8475983d/manila/share/drivers/ganesha/manager.py#L205
#
# The key differences is the lack of other Ganesha control code
# and the removal of oslo's JSON helpers.


import io
import json
import re
import sys


IWIDTH = 4


def _conf2json(conf):
    """Convert Ganesha config to JSON."""

    # tokenize config string
    token_list = [io.StringIO()]
    state = {
        'in_quote': False,
        'in_comment': False,
        'escape': False,
    }

    cbk = []
    for char in conf:
        if state['in_quote']:
            if not state['escape']:
                if char == '"':
                    state['in_quote'] = False
                    cbk.append(lambda: token_list.append(io.StringIO()))
                elif char == '\\':
                    cbk.append(lambda: state.update({'escape': True}))
        else:
            if char == "#":
                state['in_comment'] = True
            if state['in_comment']:
                if char == "\n":
                    state['in_comment'] = False
            else:
                if char == '"':
                    token_list.append(io.StringIO())
                    state['in_quote'] = True
        state['escape'] = False
        if not state['in_comment']:
            token_list[-1].write(char)
        while cbk:
            cbk.pop(0)()

    if state['in_quote']:
        raise RuntimeError("Unterminated quoted string")

    # jsonify tokens
    js_token_list = ["{"]
    for tok in token_list:
        tok = tok.getvalue()

        if tok[0] == '"':
            js_token_list.append(tok)
            continue

        for pat, s in [
                # add omitted "=" signs to block openings
                (r'([^=\s])\s*{', '\\1={'),
                # delete trailing semicolons in blocks
                (r';\s*}', '}'),
                # add omitted semicolons after blocks
                (r'}\s*([^}\s])', '};\\1'),
                # separate syntactically significant characters
                (r'([;{}=])', ' \\1 ')]:
            tok = re.sub(pat, s, tok)

        # map tokens to JSON equivalents
        for word in tok.split():
            if word == "=":
                word = ":"
            elif word == ";":
                word = ','
            elif word in ['{', '}'] or  \
                    re.search(r'\A-?[1-9]\d*(\.\d+)?\Z', word):
                pass
            else:
                word = json.dumps(word)
            js_token_list.append(word)
    js_token_list.append("}")

    # group quoted strings
    token_grp_list = []
    for tok in js_token_list:
        if tok[0] == '"':
            if not (token_grp_list and isinstance(token_grp_list[-1], list)):
                token_grp_list.append([])
            token_grp_list[-1].append(tok)
        else:
            token_grp_list.append(tok)

    # process quoted string groups by joining them
    js_token_list2 = []
    for x in token_grp_list:
        if isinstance(x, list):
            x = ''.join(['"'] + [tok[1:-1] for tok in x] + ['"'])
        js_token_list2.append(x)

    return ''.join(js_token_list2)


def _dump_to_conf(confdict, out=sys.stdout, indent=0):
    """Output confdict in Ganesha config format."""
    if isinstance(confdict, dict):
        for k, v in confdict.items():
            if v is None:
                continue
            if isinstance(v, dict):
                out.write(' ' * (indent * IWIDTH) + k + ' ')
                out.write("{\n")
                _dump_to_conf(v, out, indent + 1)
                out.write(' ' * (indent * IWIDTH) + '}')
            elif isinstance(v, list):
                for item in v:
                    out.write(' ' * (indent * IWIDTH) + k + ' ')
                    out.write("{\n")
                    _dump_to_conf(item, out, indent + 1)
                    out.write(' ' * (indent * IWIDTH) + '}\n')
            # The 'CLIENTS' Ganesha string option is an exception in that it's
            # string value can't be enclosed within quotes as can be done for
            # other string options in a valid Ganesha conf file.
            elif k.upper() == 'CLIENTS':
                out.write(' ' * (indent * IWIDTH) + k + ' = ' + v + ';')
            else:
                out.write(' ' * (indent * IWIDTH) + k + ' ')
                out.write('= ')
                _dump_to_conf(v, out, indent)
                out.write(';')
            out.write('\n')
    else:
        dj = json.dumps(confdict)
        out.write(dj)


def parseconf(conf):
    """Parse Ganesha config.
    Both native format and JSON are supported.
    Convert config to a (nested) dictionary.
    """
    def list_to_dict(src_list):
        # Convert a list of key-value pairs stored as tuples to a dict.
        # For tuples with identical keys, preserve all the values in a
        # list. e.g., argument [('k', 'v1'), ('k', 'v2')] to function
        # returns {'k': ['v1', 'v2']}.
        dst_dict = {}
        for i in src_list:
            if isinstance(i, tuple):
                k, v = i
                if isinstance(v, list):
                    v = list_to_dict(v)
                if k in dst_dict:
                    dst_dict[k] = [dst_dict[k]]
                    dst_dict[k].append(v)
                else:
                    dst_dict[k] = v
        return dst_dict

    try:
        # allow config to be specified in JSON --
        # for sake of people who might feel Ganesha config foreign.
        d = json.loads(conf)
    except ValueError:
        # Customize JSON decoder to convert Ganesha config to a list
        # of key-value pairs stored as tuples. This allows multiple
        # occurrences of a config block to be later converted to a
        # dict key-value pair, with block name being the key and a
        # list of block contents being the value.
        li = json.loads(_conf2json(conf), object_pairs_hook=lambda x: x)
        d = list_to_dict(li)
    return d


def mkconf(confdict):
    """Create Ganesha config string from confdict."""
    s = io.StringIO()
    _dump_to_conf(confdict, s)
    return s.getvalue()
