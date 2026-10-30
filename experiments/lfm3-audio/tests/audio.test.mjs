import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { PCM16Encoder, pcmToBase64, base64ToPCM, microphoneError } from "../app/audio.js";

test("44.1/48/96 kHz input keeps packet cadence across 128-sample worklet blocks", () => {
  for (const rate of [44100, 48000, 96000]) {
    const encoder = new PCM16Encoder(rate);
    const input = Float32Array.from({ length: rate }, (_, i) => Math.sin(i * 440 * 2 * Math.PI / rate) * .5);
    const packets = [];
    for (let start = 0; start < input.length; start += 128)
      packets.push(...encoder.process(input.subarray(start, start + 128)));
    const last = encoder.flush();
    assert.equal(packets.length, 10);
    assert.equal(last.length, 0);
    const decoded = base64ToPCM(pcmToBase64(packets[0]));
    const rms = Math.sqrt(decoded.reduce((sum, x) => sum + x * x, 0) / decoded.length);
    assert.ok(rms > .34 && rms < .36);
  }
});

test("a partial final packet is flushed exactly once; independent holds do not leak samples", () => {
  const encoder = new PCM16Encoder(48000);
  assert.equal(encoder.process(new Float32Array(1440).fill(.25)).length, 0);
  assert.equal(encoder.flush().length, 480);
  assert.equal(encoder.flush().length, 0);
  const next = new PCM16Encoder(48000);
  next.process(new Float32Array(300));
  assert.ok(next.flush().every((sample) => sample === 0));
});

test("PCM wire format is signed little endian, finite and bounded", () => {
  const source = Int16Array.of(-32768, -1, 0, 32767);
  assert.equal(Buffer.from(pcmToBase64(source), "base64").toString("hex"), "0080ffff0000ff7f");
  assert.deepEqual([...base64ToPCM(pcmToBase64(source))], [-1, -1 / 32768, 0, 32767 / 32768]);
  const encoder = new PCM16Encoder(16000);
  encoder.process(Float32Array.of(-10, 10, NaN, Infinity));
  assert.deepEqual([...encoder.flush()], [-32768, 32767, 0, 0]);
  assert.throws(() => base64ToPCM("AA=="));
  assert.throws(() => new PCM16Encoder(8000));
});

test("microphone denial and missing devices have distinct actionable messages", () => {
  assert.match(microphoneError({ name: "NotAllowedError" }), /permissions/);
  assert.match(microphoneError({ name: "NotFoundError" }), /No microphone/);
  assert.match(microphoneError({ name: "NotReadableError" }), /another app/);
});

test("worklet streams only while held, flushes before stop, and never reuses old audio", () => {
  let Capture;
  const messages = [];
  class AudioWorkletProcessor {
    constructor() { this.port = { postMessage: (message) => messages.push(message) }; }
  }
  const source = readFileSync(new URL("../app/capture.js", import.meta.url), "utf8")
    .replace(/^import .*;\n/, "");
  vm.runInNewContext(source, { PCM16Encoder, AudioWorkletProcessor, sampleRate: 48000,
    registerProcessor: (_name, implementation) => { Capture = implementation; } });
  const worklet = new Capture();
  const input = [[new Float32Array(4800).fill(.25)]];
  worklet.process(input);
  assert.equal(messages.length, 0);
  worklet.port.onmessage({ data: { type: "start", turn: 1, rate: 16000 } });
  worklet.process(input);
  worklet.process([[new Float32Array(300).fill(.5)]]);
  worklet.port.onmessage({ data: { type: "stop", turn: 1 } });
  assert.deepEqual(messages.map((message) => [message.type, message.turn, message.samples?.length]),
    [["audio", 1, 1600], ["audio", 1, 100], ["stopped", 1, undefined]]);
  worklet.process(input);
  assert.equal(messages.length, 3);
  worklet.port.onmessage({ data: { type: "start", turn: 2, rate: 16000 } });
  worklet.process([[new Float32Array(300)]]);
  worklet.port.onmessage({ data: { type: "stop", turn: 2 } });
  assert.ok(messages[3].samples.every((sample) => sample === 0));
});
