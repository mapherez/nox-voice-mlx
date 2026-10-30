# LFM3 Audio experiment

A separate, local push-to-talk demonstration with optional MCP tools. Hold **Space** (or the on-screen button) to speak; release it to request a spoken response. One request runs at a time. Conversation context lasts until you disconnect.

The main application and its configuration are independent of this experiment.

The [multi-model engine contract](../../docs/voice-engine-contract.md) defines the
agreed integration into the main webapp, including separate provider profiles,
an executor LLM and interruptible background tasks. It supersedes this demo's
temporary behavior as the production target; those changes are not implemented
by this experiment or by the contract's publication.

## Start here

Requirements: macOS on Apple Silicon, Python 3.9 or later already installed, a microphone, and a browser with AudioWorklet support. Chrome is suitable. No `npm install`, `uv`, Python packages, Homebrew installation, account, or token is needed.

From the repository root:

```sh
cd experiments/lfm3-audio
python3 experiment.py prepare
python3 experiment.py run
```

`prepare` downloads the pinned runtime and two model files, verifies their SHA-256 hashes, and keeps them here. Allow approximately **2.5 GB** of disk space, plus download staging space. Downloads are sequential; a file is moved to its final location only after verification. Already valid model files are reused. An interrupted download is removed on normal cancellation.

`run` verifies the model files and loads them locally. It does not download anything. Wait until the terminal prints:

```text
Open http://localhost:8765
```

Open that URL, select **Connect microphone**, and allow microphone access. Hold Space while speaking and release it to send. You can also hold the on-screen button with a mouse or touch. Wait for the reply to finish before speaking again. Each hold is limited to 60 seconds. Losing focus while recording or hiding the page disconnects the session so recording cannot remain stuck on.

**Disconnect** releases the microphone and the conversation. The model stays loaded until you stop the terminal process. Reconnecting starts a fresh conversation. The sphere responds only to the assistant's audio. Replies shown on the page are assistant text; this demonstration does not separately transcribe your microphone.

If the browser does not show a permission prompt, allow the microphone in the site's permissions. Also check the browser's system microphone permission and that an input device is available.

## Connect an app through MCP

No additional installation or model download is required for MCP. Start the experiment with the same `run` command.

1. Open **MCP** in the page header, before connecting the microphone.
2. Paste the complete **Streamable HTTP** endpoint URL from your app and select **Connect MCP**.
3. Check the discovered tool names and descriptions, then close the dialog.
4. Select **Connect microphone**. Hold Space and ask the model to perform an action supported by those tools.
5. Inspect **Tool activity** for the actual arguments, result, and execution status. The model receives that result and can respond by voice or make the next call.

This experiment supports **unauthenticated HTTP/HTTPS endpoints**, including localhost URLs. The HTTP client runs alongside the model, so `localhost` refers to that same computer. The URL is entered in the interface, not an environment variable or configuration file. It is retained in server memory until MCP is disconnected or the experiment stops. The experiment never searches for endpoints or reads another application's configuration.

**Connect MCP exposes the listed tools to the voice conversation.** Requested actions execute directly; there is no separate confirmation dialog for each tool. The integration is generic: it discovers the tool schemas rather than hardcoding an application's commands. Arguments are parsed as JSON objects, and the MCP server validates its own input schemas.

Disconnect the microphone before changing MCP connections or refreshing the tool catalogue. After changing it, reconnect the microphone to start a conversation with the new tools. Turning off an app's MCP server causes subsequent calls to fail; the experiment does not restart that server or silently reconnect and retry actions.

**Disconnect microphone** ends the voice session and prevents further queued calls. A tool call already sent to another app may still finish; disconnecting cannot undo its side effects. Its result remains visible if it returns while the page is open. **Disconnect MCP** also terminates the MCP session where the server supports it. Ctrl+C stops the experiment's processes, not the external app.

Calls are identified within each conversation. Repeated delivery of the same call ID returns its recorded outcome instead of running it again. A lost connection or incomplete response is marked **OUTCOME UNKNOWN · NO RETRY**; remaining queued actions stop and the model's next response has tools disabled. The model's spoken claim is not a substitute for the result shown in Tool activity.

The client supports JSON and SSE responses to POST, protocol negotiation, session IDs and paginated tool discovery. This first experiment does not implement OAuth, stdio transport, resource browsing, sampling, elicitation, MCP tasks or a persistent notification subscription. Reconnect MCP to refresh tools. The catalogue is limited to 128 tools and 60,000 serialized characters. Tool loops are limited to six rounds and twelve calls per spoken request; conversation context is 32K tokens. Large tool results are explicitly abbreviated before being given to the model, and binary results are omitted. Limits fail visibly rather than silently exposing only a subset of the tools.

## Explicit delegation to a local LLM

This is a separate, explicitly selected request path. It does not use Laya, LiteLLM, a classifier, or automatic model selection. The normal **Direct MCP** path remains available.

The current adapter uses the **LM Studio native `POST /api/v1/chat` API**, with no authentication. Start the model server yourself; the experiment does not start, stop, download or configure your external LLM.

1. Connect your app under **MCP** as above, with the microphone disconnected.
2. Expand **Explicit local LLM delegation** in the same dialog.
3. Enter the full chat API URL, for example `http://localhost:1234/api/v1/chat`.
4. Enter the exact **Model ID** shown by LM Studio.
5. Select the **Target MCP tool**, for example the connected app's note-creation tool. The names come from its discovered catalogue.
6. Select **Save delegation**. This selects **Local LLM → MCP** under **Request path** on the main page.
7. Close the dialog, connect the microphone and speak your request: for example, ask for a well-organized note with a specific title, language, headings and a checklist.

The voice model passes the request to one delegation tool. The local LLM receives that request together with the selected MCP tool's description and input schema. It generates the **complete argument object**, including formatted content. The backend stores it temporarily in memory, then sends that same object directly to the selected MCP tool. After success, the voice model receives a short execution receipt and title, rather than the generated body. The app plays a fixed, short completion message using the connected app name, for example “Done. The result is available in NoteX.” A text-only, bounded model continuation closes the tool-result turn; its output is not displayed or played. This prevents the conversational model from narrating or inventing the note content in the completion message. Full arguments and results remain visible in Tool activity; the voice model never copies the generated content back into another write request.

Before contacting the local LLM, the app plays a short spoken notice: “I'll pass that to the local model. Give me a moment.” The LFM3 runtime generates both the progress notice and the short completion clip when connecting the microphone in delegated mode, before opening the conversation; they stay only in browser memory and are reused during that connection. Connecting can therefore take a little longer. The LLM request starts only after playback ends. Disconnecting during the notice prevents generation and the MCP action. If preparing or playing the notice fails, the request fails visibly. Direct MCP mode does not prepare or play these clips. The completion clip plays only after confirmed success; failed or uncertain results use the normal error explanation instead. This initial experiment uses fixed phrases; there are no workflow settings yet.

**Tool activity** shows the requested task, local model ID, actual generated arguments sent to MCP, and the returned result. While waiting, the page distinguishes **Generating with local LLM…** from **Saving through MCP…**. The MCP server still validates its own input schema. If generation fails or returns invalid JSON, nothing is sent to the MCP tool. Formatting quality depends on the selected model and the destination tool's accepted formats.

The voice session receives the connected app name from MCP metadata. When that app is NoteX, a small pronunciation hint (“no tex”) and contextual aliases help the model interpret the app reference. This is prompt guidance, not a guarantee of speech recognition accuracy, and does not rewrite dictated note titles or content. The actual destination is always the configured MCP connection and selected tool; a misheard name cannot open or switch to another application.

The delegated path exposes only the delegation tool to the voice model for that conversation. Each spoken request can perform one selected MCP action; after its result, further tool use is disabled for that request to prevent a second write. The current UI still waits for that request to finish before accepting another spoken turn; background delegation and concurrent commands are not implemented yet. Disconnect the microphone and choose **Direct MCP** to return to ordinary voice-controlled tools.

Configuration and generated drafts remain in memory only. Reconnecting MCP clears the delegation configuration, so reconfigure it before starting a new delegated conversation. If you disconnect voice while generation is pending, its draft is discarded without sending the MCP action. A save already dispatched to MCP may still finish; its side effects cannot be undone by disconnecting. Changing configuration invalidates unexecuted drafts. Completed/unknown saves are recorded so re-delivering the same draft cannot repeat them.

The adapter requests `store: false`, `integrations: []`, and non-streaming text output from LM Studio. It ignores reasoning output and never enables LM Studio's own MCP integrations; the experiment's existing MCP client performs the save. LM Studio's own operational logging remains under its configuration. No new packages or model files are installed by enabling delegation. Generation has a 150-second timeout and a 4096-token output limit; overly long, incomplete or invalid output fails visibly instead of being saved.

## Stop

Press **Ctrl+C** in the terminal running `experiment.py run`. The runner closes its connections, stops its own llama-server process, and unloads the model. Nothing is registered to start at login or run in the background. As with any process, forcibly killing the runner with SIGKILL bypasses its cleanup; use Ctrl+C for a normal stop.

To choose another local port:

```sh
python3 experiment.py run --port 8766
```

Open the exact localhost URL printed by the runner. The demonstration binds only to `127.0.0.1`; it is intended for a browser on the same computer. The private runtime uses a temporary local port and a per-run credential managed in memory. There is no credential to configure.

## Files and cleanup

Everything downloaded or explicitly generated by the experiment is kept under this directory:

| Path | Contents |
| --- | --- |
| `app/` | Static interface, microphone capture and playback |
| `experiment.py` | Preparation and manual process lifecycle; Python standard library only |
| `mcp_client.py` | In-memory Streamable HTTP client and execution receipts; standard library only |
| `delegation.py` | Explicit native-API generation and one selected MCP execution; standard library only |
| `artifacts.json` | Exact runtime/model URLs, revision, sizes and SHA-256 hashes |
| `runtime/` | Extracted llama.cpp runtime and its libraries |
| `models/` | Two GGUF files, approximately 2.42 GB total |
| `.cache/` | Experiment-specific cache location |
| `.tmp/` | Download staging and a small process lock file |
| `tests/` | Isolated verification tools |

Audio is streamed through memory. The experiment does not save recordings, conversation history, MCP/LLM URLs, tool arguments/results, or request logs. It loads local model files with offline mode enabled; MCP requests go only to the URL explicitly connected in the interface. Delegated generation goes only to the explicitly configured LLM chat endpoint. It has no external frontend assets and never reads the main application's configuration or certificates. Browser data and caches managed internally by the operating system remain under their usual system control.

After stopping the experiment, you can remove `runtime/`, `models/`, `.cache/`, and `.tmp/` to reclaim downloaded/generated files, or delete this entire experiment directory. There is no package manager uninstall or service registration to undo. You may also copy the entire directory elsewhere **while stopped**; paths are resolved relative to `experiment.py`, rather than a fixed installation path.

## Runtime and model

- [llama.cpp b11960](https://github.com/ggml-org/llama.cpp/releases/tag/b11960), precompiled arm64 runtime. The original bundle includes its notices.
- [LiquidAI/LFM3-Audio-2B-GGUF](https://huggingface.co/LiquidAI/LFM3-Audio-2B-GGUF), revision `642697a5597a675cb819ed236ac415c1e60a4f6f`.
- Q8_0 language weights and the BF16 multimodal projector, prioritizing audio quality for this first experiment.
- [Realtime protocol](https://github.com/ggml-org/llama.cpp/blob/b11960/tools/server/README.md#realtime-conversation): 16 kHz PCM16 input and 24 kHz PCM16 output; explicit end of turn on release.

Without MCP, the initial prompt is the model's documented `Respond with interleaved text and audio.` With tools, it uses the documented function-calling prompt and automatic output modality so the model can choose a tool call before speaking. Portuguese input can be tried; the response language and accent remain model behavior to evaluate. No additional speech recognition or text-to-speech model is installed.

## Development checks

From this directory, with no running experiment required:

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/audio.test.mjs tests/tools.test.mjs
```

The tests create disposable files only under `.tmp/`. Node is needed only for JavaScript development tests, not for running the demonstration.

With the experiment already running, this optional live test generates a synthetic spoken question and sends it back to the model. It uses no microphone and saves no audio:

```sh
python3 -B tests/smoke.py
```

For an explicit live tool test, the following starts its **own** model process and a disposable MCP app on temporary localhost ports. It synthesizes a spoken request, lets the model select a tool, creates one in-memory test note, and verifies a spoken final response. It never connects to a real app or uses a microphone. Its own processes and temporary files are closed afterward:

```sh
python3 -B tests/live_mcp.py
```

`tests/browser_host.py` runs the optional Playwright harness against its own HTTP server and MCP fixture, with a disposable browser profile and synthetic microphone. Pass the path to a locally installed `playwright/index.mjs` as its argument. Playwright is not a runtime dependency.

`tests/live_delegation.py` verifies the voice-to-delegation path with its own model process, synthetic speech and disposable MCP app. Without arguments it uses a simulated LLM endpoint. To explicitly test a running LM Studio model, provide `--url <chat-api-url> --model <model-id>`; it still writes only to its own in-memory MCP fixture and leaves the external LLM server running. This optional development test also uses Node to read the production voice tool configuration.

