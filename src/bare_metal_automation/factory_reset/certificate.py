"""Sanitisation certificate generator.

Produces per-device audit certificates documenting what was erased, by whom,
when, and how.  The certificate is stored as a JSON record and can also be
rendered as a plain-text human-readable document.

Certificates are generated after a successful factory reset and stored in a
timestamped directory so they can be archived for compliance.

Certificate fields
------------------
  certificate_id  : UUID-based unique identifier
  device_serial   : Hardware serial number
  device_hostname : Intended hostname
  device_platform : Platform string (cisco_ios, hpe_dl380_gen10, etc.)
  method          : Sanitisation method (sed_crypto, write_erase, etc.)
  timestamp       : ISO-8601 UTC timestamp of the operation
  operator        : Username / identity performing the reset (from env/config)
  deployment_name : Name of the deployment that was reset
  checksum        : SHA-256 of the certificate content (tamper evidence)
  success         : Whether the sanitisation succeeded
  notes           : Free-text notes (error messages, warnings, etc.)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from bare_metal_automation.models import DiscoveredDevice

logger = logging.getLogger(__name__)

# Who is performing the reset (pulled from env if set)
_OPERATOR = os.environ.get("BMA_OPERATOR", "unknown-operator")


@dataclass
class SanitisationCertificate:
    """A single device sanitisation certificate."""

    certificate_id: str
    device_serial: str
    device_hostname: str
    device_platform: str
    method: str
    timestamp: str
    operator: str
    deployment_name: str
    success: bool
    notes: str = ""
    checksum: str = ""

    def __post_init__(self) -> None:
        # Compute checksum over all fields except the checksum itself
        if not self.checksum:
            self.checksum = self._compute_checksum()

    def _compute_checksum(self) -> str:
        """SHA-256 over the certificate payload (excluding the checksum field)."""
        payload = {k: v for k, v in asdict(self).items() if k != "checksum"}
        raw = json.dumps(payload, sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent)

    def to_human_readable(self) -> str:
        """Return a plain-text certificate suitable for printing / archiving."""
        lines = [
            "=" * 70,
            "  BARE METAL AUTOMATION — DATA SANITISATION CERTIFICATE",
            "=" * 70,
            f"  Certificate ID : {self.certificate_id}",
            f"  Issued by      : {self.operator}",
            f"  Date/Time      : {self.timestamp}",
            "-" * 70,
            "  DEVICE DETAILS",
            f"  Serial Number  : {self.device_serial}",
            f"  Hostname       : {self.device_hostname}",
            f"  Platform       : {self.device_platform}",
            f"  Deployment     : {self.deployment_name}",
            "-" * 70,
            "  SANITISATION",
            f"  Method         : {self.method}",
            f"  Result         : {'PASSED' if self.success else 'FAILED'}",
        ]
        if self.notes:
            lines.append(f"  Notes          : {self.notes}")
        lines += [
            "-" * 70,
            f"  Checksum (SHA-256): {self.checksum}",
            "=" * 70,
        ]
        return "\n".join(lines)


class CertificateGenerator:
    """Generate and persist sanitisation certificates."""

    def __init__(
        self,
        deployment_name: str,
        output_dir: str | Path = "sanitisation-certs",
        operator: str | None = None,
    ) -> None:
        self.deployment_name = deployment_name
        self.output_dir = Path(output_dir)
        self.operator = operator or _OPERATOR

    def generate(
        self,
        device: DiscoveredDevice,
        method: str,
        success: bool,
        notes: str = "",
    ) -> SanitisationCertificate:
        """Create a certificate for a single device sanitisation event."""
        return SanitisationCertificate(
            certificate_id=str(uuid.uuid4()),
            device_serial=device.serial or "UNKNOWN",
            device_hostname=device.intended_hostname or device.hostname or device.ip,
            device_platform=device.device_platform or "",
            method=method,
            timestamp=datetime.now(timezone.utc).isoformat(),
            operator=self.operator,
            deployment_name=self.deployment_name,
            success=success,
            notes=notes,
        )

    def save(self, cert: SanitisationCertificate) -> Path:
        """Write a certificate to disk as JSON.

        Filename: ``{output_dir}/{timestamp}_{serial}.json``
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{ts}_{cert.device_serial}.json"
        path = self.output_dir / filename

        path.write_text(cert.to_json())
        logger.info(f"Certificate written: {path}")
        return path

    def save_text(self, cert: SanitisationCertificate) -> Path:
        """Write the human-readable certificate alongside the JSON version."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{ts}_{cert.device_serial}.txt"
        path = self.output_dir / filename

        path.write_text(cert.to_human_readable())
        logger.info(f"Certificate (text) written: {path}")
        return path

    def generate_batch(
        self,
        devices: list[DiscoveredDevice],
        method: str,
        successes: dict[str, bool],
    ) -> list[SanitisationCertificate]:
        """Generate and save certificates for multiple devices.

        Args:
            devices:   List of devices that were sanitised.
            method:    Sanitisation method applied to all devices.
            successes: ``{serial: bool}`` result per device.

        Returns:
            List of generated certificates.
        """
        certs: list[SanitisationCertificate] = []
        for device in devices:
            serial = device.serial or device.ip
            success = successes.get(serial, False)
            cert = self.generate(device, method, success)
            self.save(cert)
            self.save_text(cert)
            certs.append(cert)

        passed = sum(1 for c in certs if c.success)
        logger.info(
            f"Sanitisation certificates: {passed}/{len(certs)} passed, "
            f"saved to {self.output_dir}"
        )
        return certs
