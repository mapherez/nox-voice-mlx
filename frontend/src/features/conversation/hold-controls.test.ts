// @vitest-environment jsdom
import { describe, expect, it, vi } from "vite-plus/test";
import { HoldControls } from "./hold-controls";
describe("hold ownership", () => {
  it("commits a normal release but never commits a recording during teardown", () => {
    const audio = {
      active: true,
      recording: true,
      muted: false,
      beginHold: vi.fn(),
      endHold: vi.fn(),
      interruptSpeech: vi.fn(),
    };
    const controls = new HoldControls(audio, () => false, vi.fn());
    const detach = controls.attach();
    document.dispatchEvent(
      new KeyboardEvent("keydown", {
        code: "Space",
        bubbles: true,
        cancelable: true,
      }),
    );
    expect(audio.beginHold).toHaveBeenCalledOnce();
    detach();
    expect(audio.endHold).not.toHaveBeenCalled();
    const detachAgain = controls.attach();
    document.dispatchEvent(
      new KeyboardEvent("keydown", {
        code: "Space",
        bubbles: true,
        cancelable: true,
      }),
    );
    document.dispatchEvent(
      new KeyboardEvent("keyup", {
        code: "Space",
        bubbles: true,
        cancelable: true,
      }),
    );
    expect(audio.beginHold).toHaveBeenCalledTimes(2);
    expect(audio.endHold).toHaveBeenCalledOnce();
    detachAgain();
  });
  it("releases a combined hold only when both pointer and Space have been released", () => {
    const audio = {
      active: true,
      recording: true,
      muted: false,
      beginHold: vi.fn(),
      endHold: vi.fn(),
      interruptSpeech: vi.fn(),
    };
    const controls = new HoldControls(audio, () => false, vi.fn()),
      detach = controls.attach();
    document.dispatchEvent(
      new KeyboardEvent("keydown", { code: "Space", bubbles: true }),
    );
    controls.beginPointer(1);
    document.dispatchEvent(
      new KeyboardEvent("keyup", { code: "Space", bubbles: true }),
    );
    expect(audio.endHold).not.toHaveBeenCalled();
    controls.endPointer(1);
    expect(audio.endHold).toHaveBeenCalledOnce();
    detach();
  });
});
