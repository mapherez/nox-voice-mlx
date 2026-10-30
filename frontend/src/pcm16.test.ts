import { describe, expect, it } from "vitest";
import { base64ToPCM, PCM16Encoder, pcmToBase64 } from "./pcm16";

describe("PCM16 audio transport", () => {
  it.each([44100, 48000, 96000])(
    "preserves sample count across arbitrary worklet boundaries at %i Hz",
    (rate) => {
      const encoder = new PCM16Encoder(rate);
      const frames: Int16Array[] = [];
      const samples = new Float32Array(rate).fill(0.25);
      for (let index = 0; index < samples.length; index += 128)
        frames.push(...encoder.process(samples.subarray(index, index + 128)));
      frames.push(encoder.flush());
      expect(frames.reduce((count, frame) => count + frame.length, 0)).toBe(
        16000,
      );
      expect(frames.filter((frame) => frame.length === 1600)).toHaveLength(10);
      expect(frames[0][0]).toBeCloseTo(8192, 0);
    },
  );

  it("flushes the last partial packet and encodes explicit little-endian PCM", () => {
    const encoder = new PCM16Encoder(48000);
    expect(encoder.process(new Float32Array(300).fill(-1))).toEqual([]);
    const tail = encoder.flush();
    expect(tail).toHaveLength(100);
    expect(tail[0]).toBe(-32768);
    expect(encoder.flush()).toHaveLength(0);
    const encoded = pcmToBase64(new Int16Array([-32768, -1, 0, 32767]));
    expect(Array.from(base64ToPCM(encoded))).toEqual([
      -1,
      -1 / 32768,
      0,
      32767 / 32768,
    ]);
  });

  it("clips invalid samples and rejects malformed or unbounded output packets", () => {
    const encoder = new PCM16Encoder(16000, 16000, 4);
    expect(
      Array.from(encoder.process(new Float32Array([NaN, Infinity, 2, -2]))[0]),
    ).toEqual([0, 0, 32767, -32768]);
    for (const value of ["!", btoa("a"), "a".repeat(10000)])
      expect(() => base64ToPCM(value)).toThrow();
  });
});
