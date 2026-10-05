// A light tap on tab and sheet actions. iOS Safari has no vibrate API; since iOS 18 a native
// switch control (<input type="checkbox" switch>) gives a tap when it is toggled, so a hidden one
// is clicked from inside the visitor's own tap. Where neither works (older iOS, desktop) this
// does nothing, and the app behaves exactly the same.
let toggle: HTMLLabelElement | null = null;

export function tap() {
  try {
    if (typeof navigator.vibrate === "function") { navigator.vibrate(8); return; }   // Android
    if (!toggle) {
      const input = document.createElement("input");
      input.type = "checkbox";
      input.setAttribute("switch", "");
      input.tabIndex = -1;
      input.setAttribute("aria-hidden", "true");
      toggle = document.createElement("label");
      toggle.setAttribute("aria-hidden", "true");
      toggle.style.cssText = "position:fixed;left:-100px;top:-100px;width:1px;height:1px;opacity:.01;pointer-events:none;";
      toggle.append(input);
      document.body.append(toggle);
    }
    toggle.click();
  } catch { /* no haptics here: fine */ }
}
