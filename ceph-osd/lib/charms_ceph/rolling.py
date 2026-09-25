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

"""The host ordering and durable handoff used by Ceph rolling upgrades.

The caller supplies an ordered, frozen participant list and a shared generation
identifier. There must be exactly one writer per participant (e.g. Juju's unit
hook serialization). This is an ordered handoff, not a distributed mutex or a
lease: a missing/failed participant is never bypassed by the strict turn check.

Release upgrades retain their legacy watchdog waiting policy. Other callers can
use ``check_turn`` between hooks to avoid blocking delivery of relation events.
"""

import time


class RollingError(Exception):
    """A rolling operation cannot safely proceed."""


class RollingWait(Exception):
    """Another participant has not completed its operation yet."""


def position(participants, participant):
    """Find a participant in an explicitly ordered, unique cohort."""
    if len(set(participants)) != len(participants):
        raise ValueError('duplicate rolling-operation participants')
    try:
        return participants.index(participant)
    except ValueError:
        raise ValueError("Host '{}' is not a rollout participant".format(
            participant))


class RollingOperation:
    """Run a callback using the upgrade start/alive/done marker protocol.

    ``read(key)`` returns None only for an absent key; transport and permission
    errors must raise. ``write(key, value)`` must acknowledge a durable write.
    Namespace/generation allocation and participant discovery belong to the
    caller. Never reuse a generation for a new operation.
    """

    def __init__(self, namespace, generation, read, write, now=None):
        self.namespace = namespace
        self.generation = generation
        self.read = read
        self.write = write
        self.now = now or time.time

    def key(self, participant, phase):
        return '{}_{}_{}_{}'.format(
            self.namespace, participant, self.generation, phase)

    def completed(self, participant):
        return self.read(self.key(participant, 'done')) is not None

    def check_turn(self, participants, participant, created, timeout=1800):
        """Return False if already done, True if admitted, otherwise raise.

        Check *every* predecessor, not just the nearest one. Completion is not
        inferred from liveness, elapsed time, missing hosts or a failed RPC.
        An admitted participant may retry its own interrupted/failed operation;
        no successor is admitted until it explicitly reports success.
        """
        index = position(participants, participant)
        if self.completed(participant):
            return False
        for previous in participants[:index]:
            if self.completed(previous):
                continue
            failure = self.read(self.key(previous, 'failed'))
            if failure:
                raise RollingError('rollout stopped at {}: {}'.format(
                    previous, failure))
            if self.now() - created >= timeout:
                raise RollingError('rollout timed out waiting for {}; '
                                   'not restarting this host'.format(previous))
            raise RollingWait('waiting for {} to finish the resource '
                              'rollout'.format(previous))
        return True

    def run(self, participant, callback, watchdog_factory,
            record_failure=True):
        """Execute a local operation; publish done only after success.

        A retry must revalidate local enforcement/readiness before returning.
        A crash or failed completion write leaves no done marker, fencing all
        successors. Old failure markers need not be deleted: done takes
        precedence once the owner has successfully recovered.
        """
        self.write(self.key(participant, 'start'), self.now())
        dog = watchdog_factory(
            kick_interval=180,
            kick_function=lambda: self.write(
                self.key(participant, 'alive'), self.now()))
        try:
            result = callback(dog.kick_the_dog)
        except Exception as exc:
            if record_failure:
                self.write(self.key(participant, 'failed'), str(exc))
            raise
        self.write(self.key(participant, 'done'), self.now())
        return result
