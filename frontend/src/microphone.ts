/** Request capture only from an explicit conversation start, with actionable errors. */
export async function requestMicrophone(): Promise<MediaStream> {
  if (!window.isSecureContext) {
    throw new Error(
      "Microphone access requires HTTPS or localhost. Open this app using a secure address.",
    );
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error(
      "Microphone capture is unavailable in this browser. Use a browser that supports microphone access and check its privacy settings.",
    );
  }
  try {
    return await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
  } catch (error) {
    const name =
      error && typeof error === "object" && "name" in error ? error.name : "";
    switch (name) {
      case "NotAllowedError":
      case "PermissionDeniedError":
      case "SecurityError":
        throw new Error(
          "Microphone access was blocked. Allow the microphone in this site's browser permissions, and allow your browser to use the microphone in your device's privacy settings. Then start the conversation again.",
        );
      case "NotFoundError":
      case "DevicesNotFoundError":
        throw new Error(
          "No microphone was found on this device. Connect or enable a microphone and select it in your browser's microphone settings. Then try again.",
        );
      case "NotReadableError":
      case "TrackStartError":
        throw new Error(
          "Your microphone could not be opened. Check that it works in your device's sound settings and close any app that may be blocking it. Then try again.",
        );
      case "OverconstrainedError":
        throw new Error(
          "Your microphone does not support the requested audio settings. Select another microphone in your browser's settings and try again.",
        );
      default:
        throw new Error(
          "Could not start the microphone. Check your browser's microphone permissions and your device's audio input, then try again.",
        );
    }
  }
}
