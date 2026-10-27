"""Private CA and mutually authenticated controller/gateway certificates."""

import ipaddress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .config import write_private


def create_certificates(directory: Path, host: str) -> Path:
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Certificates already exist. Use a new directory to rotate certificates.")
    address = ipaddress.ip_address(host)
    if not address.is_private or address.is_unspecified or address.is_multicast:
        raise ValueError("Use a specific private LAN or loopback IP address for the controller.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NoX Voice private CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    write_private(
        directory / "ca.key",
        ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    ca_pem = ca.public_bytes(serialization.Encoding.PEM)
    write_private(directory / "ca.crt", ca_pem)
    for name, usage in [
        ("server", ExtendedKeyUsageOID.SERVER_AUTH),
        ("client", ExtendedKeyUsageOID.CLIENT_AUTH),
    ]:
        key = ec.generate_private_key(ec.SECP256R1())
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"NoX Voice {name}")]))
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=True)
        )
        if name == "server":
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(address)]), critical=False
            )
        cert = builder.sign(ca_key, hashes.SHA256())
        destination = directory if name == "server" else directory / "gateway"
        write_private(destination / f"{name}.crt", cert.public_bytes(serialization.Encoding.PEM))
        write_private(
            destination / f"{name}.key",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
    write_private(directory / "gateway" / "ca.crt", ca_pem)
    return directory / "gateway"
