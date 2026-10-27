import asyncio
import ssl
from datetime import datetime, timezone

import pytest
from controller.certificates import create_certificates
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID


def test_gateway_bundle_contains_no_ca_or_server_private_key(tmp_path):
    directory = tmp_path / "tls"
    bundle = create_certificates(directory, "127.0.0.1")
    assert {p.name for p in bundle.iterdir()} == {"client.crt", "client.key", "ca.crt"}
    certificate = x509.load_pem_x509_certificate((bundle / "client.crt").read_bytes())
    assert (
        ExtendedKeyUsageOID.CLIENT_AUTH
        in certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    )
    assert certificate.not_valid_after_utc > datetime.now(timezone.utc)
    assert (bundle / "client.key").stat().st_mode & 0o077 == 0
    with pytest.raises(ValueError, match="already exist"):
        create_certificates(directory, "127.0.0.1")


async def test_mutual_tls_accepts_only_the_issued_client(tmp_path):
    directory = tmp_path / "tls"
    bundle = create_certificates(directory, "127.0.0.1")
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(directory / "server.crt", directory / "server.key")
    server_context.load_verify_locations(directory / "ca.crt")
    server_context.verify_mode = ssl.CERT_REQUIRED

    async def respond(reader, writer):
        await reader.read(1)
        writer.write(b"ok")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_server(respond, "127.0.0.1", 0, ssl=server_context) as server:
        port = server.sockets[0].getsockname()[1]
        valid = ssl.create_default_context(cafile=str(bundle / "ca.crt"))
        valid.load_cert_chain(bundle / "client.crt", bundle / "client.key")
        reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=valid)
        writer.write(b"x")
        await writer.drain()
        assert await reader.read() == b"ok"
        writer.close()
        await writer.wait_closed()
        untrusted = ssl.create_default_context(cafile=str(bundle / "ca.crt"))
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=untrusted)
            writer.write(b"x")
            await writer.drain()
            assert await reader.read() == b""
            writer.close()
        except (ssl.SSLError, ConnectionError):
            pass
