import base64
import errno
import hashlib
import http.client
import importlib.util
import io
import os
import platform
import socket
import socketserver
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lfm3_experiment", SOURCE / "experiment.py")
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class PathsAndDownloads(unittest.TestCase):
    def setUp(self):
        (SOURCE / ".tmp").mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="unit-", dir=SOURCE / ".tmp")
        self.root = Path(self.directory.name)
        self.override = patch.object(experiment, "ROOT", self.root)
        self.override.start()

    def tearDown(self):
        self.override.stop()
        self.directory.cleanup()

    def test_paths_reject_symlinks_and_parent_traversal(self):
        (self.root / "models").symlink_to(SOURCE)
        with self.assertRaises(ValueError):
            experiment.local_path("models/outside.gguf")
        with self.assertRaises(ValueError):
            experiment.local_path("../outside")

    def test_only_one_prepare_or_run_can_hold_the_lock(self):
        with experiment.exclusive_operation():
            with self.assertRaisesRegex(RuntimeError, "already"):
                with experiment.exclusive_operation():
                    self.fail("Second owner acquired the lock")
        with experiment.exclusive_operation():
            pass

    def test_environment_is_local_and_does_not_inherit_tokens(self):
        with patch.dict(os.environ, {"HF_TOKEN": "not-a-real-token", "LLAMA_ARG_TOOLS": "all"}):
            environment = experiment.runtime_environment()
        self.assertNotIn("HF_TOKEN", environment)
        self.assertNotIn("LLAMA_ARG_TOOLS", environment)
        self.assertNotIn("HOME", environment)
        for key in ["TMPDIR", "HF_HOME", "HF_HUB_CACHE", "LLAMA_CACHE", "XDG_CACHE_HOME"]:
            self.assertTrue(environment[key].startswith(str(self.root)))

    def test_download_verification_is_atomic_and_cleans_temporary_files(self):
        original, corrupt = b"existing", b"corrupt"
        destination = self.root / "models/model.gguf"
        destination.parent.mkdir()
        destination.write_bytes(original)
        artifact = {"size": len(corrupt), "sha256": "0" * 64}

        class Response(io.BytesIO):
            status = 200

        class Opener:
            def open(self, *_args, **_kwargs):
                return Response(corrupt)

        with patch.object(experiment, "build_opener", return_value=Opener()):
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                experiment.download("https://example.test/model", destination, artifact)
            self.assertEqual(destination.read_bytes(), original)
            self.assertEqual(list((self.root / ".tmp").glob("*.part")), [])
            artifact["sha256"] = hashlib.sha256(corrupt).hexdigest()
            experiment.download("https://example.test/model", destination, artifact)
        self.assertTrue(experiment.checked(destination, artifact))

    def archive(self, entries):
        archive = self.root / "runtime.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for name, link in entries:
                member = tarfile.TarInfo(name)
                if link:
                    member.type = tarfile.SYMTYPE
                    member.linkname = link
                    output.addfile(member)
                else:
                    member.size = 3
                    member.mode = 0o755
                    output.addfile(member, io.BytesIO(b"bin"))
        destination = self.root / "extracted"
        destination.mkdir(exist_ok=True)
        return archive, destination

    def test_archive_rejects_escape_paths_links_and_writes_through_links(self):
        for entries in [[("../escape", None)], [("link", "../../escape")],
                        [("folder", "target"), ("folder/bin", None)]]:
            with self.subTest(entries=entries):
                archive, destination = self.archive(entries)
                with self.assertRaises(ValueError):
                    experiment.extract_runtime(archive, destination)

    def test_runtime_extracts_local_libraries_and_executable(self):
        archive, destination = self.archive([
            ("bundle/llama-server", None), ("bundle/lib.1.dylib", None),
            ("bundle/lib.dylib", "lib.1.dylib")])
        experiment.extract_runtime(archive, destination)
        self.assertEqual((destination / "bundle/lib.dylib").read_bytes(), b"bin")
        self.assertTrue(os.access(destination / "bundle/llama-server", os.X_OK))


class MockRuntime(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class RuntimeHandler(socketserver.BaseRequestHandler):
    def handle(self):
        header = bytearray()
        while not header.endswith(b"\r\n\r\n"):
            byte = self.request.recv(1)
            if not byte:
                return
            header.extend(byte)
        self.server.received = header.decode()
        self.request.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                             b"Connection: Upgrade\r\n\r\n")
        data = self.request.recv(100)
        if data:
            self.request.sendall(data)


class RestartLifecycle(unittest.TestCase):
    def test_immediate_restart_after_server_initiated_connection_close(self):
        class PreviousServer(experiment.LocalServer):
            allow_reuse_address = False

        # On the supported platform, also test upgrading from the previous setting.
        classes = [PreviousServer, experiment.LocalServer] if platform.system() == "Darwin" else [experiment.LocalServer]
        for first_class in classes:
            with self.subTest(previous=first_class.__name__):
                server = first_class(("127.0.0.1", 0), 1, "test-only")
                address = server.server_address
                try:
                    for _ in range(3):
                        with socket.create_connection(address, timeout=3) as client:
                            accepted, _ = server.get_request()
                            with accepted:
                                accepted.shutdown(socket.SHUT_WR)
                                self.assertEqual(client.recv(1), b"")
                                client.shutdown(socket.SHUT_WR)
                                self.assertEqual(accepted.recv(1), b"")
                        server.server_close()
                        # This bind reproduces Errno 48 with the previous implementation.
                        server = experiment.LocalServer(address, 1, "test-only")
                finally:
                    server.server_close()

    def test_reuse_does_not_take_a_port_from_an_active_listener(self):
        server = experiment.LocalServer(("127.0.0.1", 0), 1, "test-only")
        try:
            with self.assertRaises(OSError) as error:
                experiment.LocalServer(server.server_address, 1, "test-only")
            self.assertEqual(error.exception.errno, errno.EADDRINUSE)
        finally:
            server.server_close()


class HttpBoundary(unittest.TestCase):
    def setUp(self):
        self.runtime = MockRuntime(("127.0.0.1", 0), RuntimeHandler)
        self.runtime.received = None
        self.runtime_thread = threading.Thread(target=self.runtime.serve_forever, daemon=True)
        self.runtime_thread.start()
        self.server = experiment.LocalServer(("127.0.0.1", 0), self.runtime.server_address[1], "ephemeral-test-key")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.close_connections()
        self.server.shutdown()
        self.server.server_close()
        self.runtime.shutdown()
        self.runtime.server_close()
        self.thread.join()
        self.runtime_thread.join()

    def request(self, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_serves_only_the_interface_and_never_model_or_config_files(self):
        status, headers, content = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Hold Space", content)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        for path in ["/artifacts.json", "/../experiment.py", "/models/model.gguf", "/v1/chat/completions"]:
            self.assertEqual(self.request(path)[0], 404)

    def test_rejects_foreign_hosts_origins_and_unattributed_websockets(self):
        self.assertEqual(self.request("/", {"Host": "foreign.test"})[0], 403)
        self.assertEqual(self.request("/", {"Origin": "https://foreign.test"})[0], 403)
        self.assertEqual(self.request("/realtime")[0], 403)
        self.assertIsNone(self.runtime.received)

    def test_websocket_tunnel_uses_only_its_own_backend_and_closes(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as client:
            request = ("GET /realtime HTTP/1.1\r\nHost: localhost:{0}\r\n"
                       "Origin: http://localhost:{0}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {1}\r\n\r\n").format(
                           self.port, base64.b64encode(b"test-16-byte-key").decode())
            client.sendall(request.encode())
            self.assertIn(b"101 Switching", client.recv(4096))
            client.sendall(b"test-frame")
            self.assertEqual(client.recv(100), b"test-frame")
            self.assertIn("Authorization: Bearer ephemeral-test-key", self.runtime.received)
        for _ in range(100):
            if not self.server.connections:
                break
            threading.Event().wait(.01)
        self.assertEqual(self.server.connections, set())


if __name__ == "__main__":
    unittest.main()
