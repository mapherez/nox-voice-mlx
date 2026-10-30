import { describe, expect, it } from "vite-plus/test";
import { PCMResampler } from "./resampler";

describe("continuous capture resampling", () => {
  for (const rate of [24000, 44100, 48000])
    it(`maintains 24 kHz cadence across 128-sample blocks at ${rate} Hz`, () => {
      const converter = new PCMResampler(rate);
      const output: Float32Array[] = [];
      const samples = rate * 2;
      for (let offset = 0; offset < samples; offset += 128) {
        const block = new Float32Array(Math.min(128, samples - offset));
        for (let i = 0; i < block.length; i++)
          block[i] = Math.sin((2 * Math.PI * 400 * (offset + i)) / rate);
        output.push(...converter.process(block));
      }
      expect(output).toHaveLength(25);
      expect(
        output.every(
          (frame) => frame.length === 1920 && frame.every(Number.isFinite),
        ),
      ).toBe(true);
      expect(output[1][0]).toBeCloseTo(0, 1);
    });
  it("never includes captured samples in muted output", () => {
    const converter = new PCMResampler(48000);
    converter.process(new Float32Array(128).fill(0.9));
    converter.clearPartial();
    const frames = converter.process(new Float32Array(4000).fill(0.9), true);
    expect(frames.length).toBeGreaterThan(0);
    expect(frames[0].every((sample) => sample === 0)).toBe(true);
  });
});
