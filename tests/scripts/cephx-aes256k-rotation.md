# CephX aes256k upgrade + rotation (CVE-2025-30156)

19.2.6 adds the `aes256k` cephx key type. Upgrade alone does not fix it — rotate every key, then disallow `aes`.

Charms do not upgrade a point release: `juju config <app> source=...` is a no-op for squid to squid, and the packages do not restart daemons. Do the upgrade by hand.

Kernel clients need Linux 7.0+ (Noble HWE 7.0). Do not rotate a kernel-client key, or cut over, while a client is on an older kernel.

## Run the test

```
tests/scripts/cephx-aes256k-rotation.sh
# override: SOURCE=ppa:... CHANNEL=squid/stable MODEL=cephx-test
```

## Manual sequence

Pre: `juju ssh ceph-mon/0 -- sudo ceph -s`   # HEALTH_OK

Order: mons (quorum leader last), OSDs, MDS, RGW, clients.

Set source (only affects new units): `juju config <app> source=ppa:johnramsden/noble-caracal-ceph-squid-sru`

Upgrade each unit — add the source, upgrade only the installed ceph packages, then restart its daemons:

```
sudo add-apt-repository -y <src>; sudo apt-get update
sudo apt-get install -y --only-upgrade <installed ceph packages>   # ceph* rbd* radosgw lib{rados,rbd,cephfs,rgw,radosstriper}* python3-{ceph,rados,rbd,rgw}*, status ii only
#   mon:  sudo systemctl restart ceph-mon@$H ceph-mgr@$H
#   osd:  sudo ceph osd set noout; sudo systemctl restart ceph-osd.target; sudo ceph osd unset noout
#   mds:  sudo systemctl restart ceph-mds@$H
#   rgw:  sudo systemctl restart ceph-radosgw@rgw.$H
```

Confirm: `juju ssh ceph-mon/0 -- sudo ceph versions`   # all 19.2.6; `ceph health detail` shows AUTH_INSECURE_* (expected)

Rotate — run each `ceph ...` on a ceph-mon unit (`juju ssh ceph-mon/0 -- <cmd>`):

```
sudo ceph mon set auth_preferred_cipher aes256k
# mon.: rotate, then import the new key into each ceph-mon unit's keyring and restart ceph-mon@HOST (the charm auths as mon.)
sudo ceph auth rotate --key-type=aes256k mon.
# action: entity=mgr runs on EVERY ceph-mon unit; osd / osd.N / mds.NAME / client.rgw.HOST run once on the leader:
juju run ceph-mon/<n> rotate-key entity=<entity>
sudo ceph tell osd.N rotate-stored-key -i -    # the action updates the keyring FILE, not the bluestore label; this fixes the label
# mds: the action sets a pending key; if the ceph-fs unit hasn't applied it, write it to /var/lib/ceph/mds/ceph-HOST/keyring and restart ceph-mds@HOST
sudo ceph mon set auth_service_cipher aes256k
sudo ceph config set mon mon_auth_allow_insecure_key false
# admin: back up client.admin first (recovery is painful), rotate, import on every ceph-mon unit, then rm the backup:
sudo ceph auth rotate --key-type=aes256k client.admin
sudo ceph auth rotate --key-type=aes256k client.X
```

Cut over:

```
sudo ceph mon set auth_allowed_ciphers aes256k
```

Rescue: `mon_auth_emergency_allowed_ciphers` in a mon's local config re-admits `aes` temporarily.

## Upstream

- CVE-2025-30156: https://docs.ceph.com/en/latest/security/CVE-2025-30156/
- Rotation procedure: https://docs.ceph.com/en/latest/rados/configuration/auth-config-ref/#upgrading-and-rotating-cephx-keys
- Health checks: https://docs.ceph.com/en/latest/rados/operations/health-checks/
- Emergency allowed ciphers: https://docs.ceph.com/en/latest/rados/configuration/auth-config-ref/#emergency-allowed-ciphers
- Squid 19.2.6 notes: https://docs.ceph.com/en/latest/releases/squid/#v19-2-6-squid
