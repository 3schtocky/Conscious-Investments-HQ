// The browser's own install offer (Chrome and Samsung Internet on Android): it fires once, early, so
// it is caught here and kept until the visitor taps our Install button.
interface InstallEvent extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: "accepted" | "dismissed" }> }

let held: InstallEvent | null = null;
let installed = false;

export function watchInstall() {
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); held = e as InstallEvent; });
  window.addEventListener("appinstalled", () => { installed = true; held = null; });
}

export const canPromptInstall = (): boolean => held !== null;
export const wasInstalled = (): boolean => installed;

/** Show the browser's install dialog. It can be used once; the answer is returned. */
export async function promptInstall(): Promise<"accepted" | "dismissed" | "unavailable"> {
  const e = held;
  if (!e) return "unavailable";
  held = null;
  try { await e.prompt(); return (await e.userChoice).outcome; } catch { return "dismissed"; }
}
