// The Android Back button (and back swipe). A web page has one history entry, so Back would leave
// the app from anywhere. Here every layer the visitor goes into (an agent sheet, a file, a More page,
// a tab other than the Floor) gets a history entry of its own, so Back peels them one at a time and
// only leaves from the Floor. The entries are kept in step with the app by `sync()`, called each frame.
import { planHistory } from "./androidModel";

export interface BackDeps {
  depth: () => number;     // how many Back presses stay in the app right now
  stepBack: () => void;    // peel one layer
}

export class AndroidBack {
  private tracked = 0;     // history entries we have pushed and not yet popped
  private pending = 0;     // history.go() calls we made ourselves and are waiting to hear about

  constructor(private d: BackDeps) {
    window.addEventListener("popstate", () => this.onPop());
  }

  sync() {
    if (this.pending > 0) return;   // wait for our own history.go() to land before touching history again
    const plan = planHistory(this.tracked, this.d.depth());
    if (plan.push) {
      for (let i = 0; i < plan.push; i++) history.pushState({ hq: this.tracked + i + 1 }, "", location.href);
      this.tracked += plan.push;
    } else if (plan.pop) {   // the app closed a layer itself (a Close button): drop its entry quietly
      this.pending++;
      this.tracked -= plan.pop;
      history.go(-plan.pop);
    }
  }

  private onPop() {
    if (this.pending > 0) { this.pending--; return; }   // that was our own history.go()
    if (this.tracked === 0) return;                      // nothing of ours left: the browser is leaving
    this.tracked--;                                      // the browser took us back one entry
    this.d.stepBack();                                   // ...and the app closes the matching layer
  }
}
