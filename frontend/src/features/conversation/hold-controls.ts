import type { LFM3Audio } from "../../lfm3-audio";

/** Owns one combined keyboard/pointer hold, independent of button focus. */
export class HoldControls {
  private pointer: number | null = null;
  private spaceHeld = false;
  constructor(
    private audio: Pick<
      LFM3Audio,
      | "active"
      | "recording"
      | "muted"
      | "beginHold"
      | "endHold"
      | "interruptSpeech"
    >,
    private modalOpen: () => boolean,
    private notice: (message: string) => void,
  ) {}
  beginPointer(id: number): boolean {
    if (this.pointer !== null) return false;
    this.pointer = id;
    this.audio.beginHold();
    return true;
  }
  endPointer(id: number): void {
    if (this.pointer !== id) return;
    this.pointer = null;
    if (!this.spaceHeld) this.audio.endHold();
  }
  private keydown = (event: KeyboardEvent) => {
    if (event.code !== "Space") return;
    if (this.spaceHeld) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    if (
      event.repeat ||
      event.isComposing ||
      this.modalOpen() ||
      event.ctrlKey ||
      event.metaKey ||
      event.altKey ||
      !this.audio.active
    )
      return;
    if (
      event.target instanceof Element &&
      event.target.closest(
        "input, textarea, select, [contenteditable], [role=textbox], [role=combobox]",
      )
    )
      return;
    event.preventDefault();
    event.stopPropagation();
    this.spaceHeld = true;
    if (this.audio.muted) {
      this.audio.interruptSpeech();
      this.notice("Unmute the microphone to speak.");
    } else this.audio.beginHold();
  };
  private keyup = (event: KeyboardEvent) => {
    if (event.code !== "Space" || !this.spaceHeld) return;
    event.preventDefault();
    event.stopPropagation();
    this.spaceHeld = false;
    if (this.pointer === null) this.audio.endHold();
  };
  reset(): void {
    this.pointer = null;
    this.spaceHeld = false;
  }
  release = (): void => {
    this.reset();
    if (this.audio.recording) this.audio.endHold();
  };
  attach(): () => void {
    document.addEventListener("keydown", this.keydown, true);
    document.addEventListener("keyup", this.keyup, true);
    window.addEventListener("blur", this.release);
    return () => {
      document.removeEventListener("keydown", this.keydown, true);
      document.removeEventListener("keyup", this.keyup, true);
      window.removeEventListener("blur", this.release);
      this.reset();
    };
  }
}
