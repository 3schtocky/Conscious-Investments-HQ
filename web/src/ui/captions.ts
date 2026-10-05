// The caption strip under the phone's floor map. It replaces speech bubbles, which are too wide
// for a phone: a few plain lines say who is talking with whom and what has just started or
// finished. Lines come from the same public events that move the floor (captionModel.ts).
import type { OfficeState } from "../state";
import { captionFor, freshLines, idleLine, pushLine, type Line } from "./captionModel";
import { h } from "./dom";

export class Captions {
  readonly root = h("div", { class: "m-captions", role: "status", "aria-live": "polite" });
  private lines: Line[] = [];
  private shown = "";

  constructor(private state: OfficeState) {
    state.on((ev) => {
      if (!ev) return;
      const text = captionFor(ev, state);
      if (!text) return;
      this.lines = pushLine(this.lines, { text, ts: Date.now() });
      this.render();
    });
    window.setInterval(() => this.render(), 2000);   // lines age out, and the resting line follows the office
    this.render();
  }

  render() {
    const fresh = freshLines(this.lines, Date.now());
    const working = [...this.state.agents.values()].filter((a) => a.status === "working").map((a) => a.nickname);
    const idle = idleLine({ workingNames: working, replaying: this.state.replaying, held: this.state.held, clockedOut: this.state.clockedOut });
    const key = fresh.length ? fresh.map((l) => l.text).join("|") : `idle:${idle}`;
    if (key === this.shown) return;
    this.shown = key;
    this.root.replaceChildren(...(fresh.length
      ? fresh.map((l, i) => h("div", { class: `m-cap${i === fresh.length - 1 ? " now" : " old"}` }, l.text))
      : [h("div", { class: "m-cap idle" }, idle)]));
  }
}
