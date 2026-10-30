"""Run browser QA on our own HTTP server and disposable MCP fixture, never an existing app."""

import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiment
from llm_fixture import LLMFixture
from mcp_fixture import MCPFixture

if __name__ == "__main__":
    with MCPFixture(sse=True, name="NoteX") as fixture, LLMFixture() as llm:
        server = experiment.LocalServer(("127.0.0.1", 0), 1, "unused-test-key")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(["node", "tests/browser.mjs", sys.argv[1]], cwd=experiment.ROOT,
                                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                         "TMPDIR": str(experiment.local_path(".tmp")) + "/",
                                         "LFM3_TEST_URL": "http://localhost:" + str(server.server_port),
                                         "LFM3_TEST_MCP_URL": fixture.url,
                                         "LFM3_TEST_LLM_URL": llm.url})
            if result.returncode == 0:
                assert [call["name"] for call in fixture.calls] == ["list_notes", "create_note"], fixture.calls
                assert len(llm.requests) == 1
                from llm_fixture import CONTENT
                assert fixture.notes[0]["content"] == CONTENT
        finally:
            server.close_connections()
            server.shutdown()
            server.server_close()
            thread.join()
    sys.exit(result.returncode)
