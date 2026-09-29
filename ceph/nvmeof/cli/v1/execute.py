import json
import re

from looseversion import LooseVersion

from ceph.ceph_admin.common import config_dict_to_string
from utility.log import Log

LOG = Log(__name__)

DISCARD_OUTPUT_STR = (
    "Enable server auth since both --client-key and --client-cert are provided"
)


class ExecuteCommandMixin:
    """Execute Command class runs NVMe CLI on Gateway Node."""

    BASE_CMD = "podman run --quiet --rm"
    NVMEOF_CLI_IMAGE = "quay.io/ceph/nvmeof-cli:latest"
    MTLS_BASE_CMD_ARGS = {
        "client-key": "/root/client.key",
        "client-cert": "/root/client.crt",
        "server-cert": "/root/server.crt",
    }

    def __local_mtls_cert_path(self):
        if not self.mtls:
            return ""

        _path = str()
        for cert in ["server.crt", "client.crt", "client.key"]:
            _path += f" -v /etc/mtls/{cert}:/root/{cert}:z "

        return _path

    def get_ceph_version(self):
        """Return the Ceph version string from the gateway node (cached)."""
        if not getattr(self, "_ceph_version_cache", None):
            out, _ = self.node.exec_command(
                sudo=True, cmd="ceph version -f json", check_ec=False
            )
            data = json.loads(out)
            version_string = data.get("version", "")
            match = re.search(r"ceph version (\S+)", version_string)
            if not match:
                raise ValueError(
                    f"Could not parse ceph version from: {version_string!r}"
                )
            self._ceph_version_cache = match.group(1)
        return self._ceph_version_cache

    def run_nvme_cli(self, entity, action, **kwargs):
        LOG.info(f"NVMe CLI command : {entity} {action}")
        base_cmd_args = kwargs.get("base_cmd_args", {})

        if self.mtls:
            base_cmd_args.update(self.MTLS_BASE_CMD_ARGS)

        if not base_cmd_args.get("server-address"):
            base_cmd_args.update({"server-address": self.node.ip_address})
        if not base_cmd_args.get("server-port"):
            base_cmd_args.update({"server-port": self.port})

        cmd_args = kwargs.get("args", {})

        # --rbd-image-size was introduced in 9.2+ (>= 20.2.1 builds)
        # Older 8.x/7.x CLI uses --size instead — translate here same as v2 does
        if "rbd-image-size" in cmd_args:
            ceph_version = self.get_ceph_version()
            if LooseVersion(ceph_version) < LooseVersion("20.2.1"):
                cmd_args["size"] = cmd_args.pop("rbd-image-size")

        command = " ".join(
            [
                self.BASE_CMD,
                self.__local_mtls_cert_path(),
                self.NVMEOF_CLI_IMAGE,
                config_dict_to_string(base_cmd_args),
                entity,
                action,
                config_dict_to_string(cmd_args),
            ]
        )
        err, out = self.node.exec_command(cmd=command, sudo=True, pretty_print=True)
        LOG.info(f"ERROR - {err or None},\nOUTPUT - {out}")

        if DISCARD_OUTPUT_STR in out:
            # TODO: This is the workaround to discard unwanted output for NVMe CLI
            #   commands with mTLS. And this workaround has to discarded once
            #   this BZ (https://bugzilla.redhat.com/show_bug.cgi?id=2304066) is fixed.
            out = out.split("\n", 1)[-1]

        return out, err
