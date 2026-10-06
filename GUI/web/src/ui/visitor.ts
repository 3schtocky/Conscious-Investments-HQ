// What a visitor to the public site gets beyond the office floor: approved newsletters to read,
// and the Captain's sign-in. Visitors can watch; they can never change anything.
import { clear, h, timeAgo } from "./dom";

interface PublicIssue { id: string; title: string; date: string; words: number; updated: number; files: Record<string, string> }

export class NewsPanel {
  items: PublicIssue[] = [];
  private loaded = false;
  constructor(private root: HTMLElement, private onChange: () => void) {}

  async refresh() {
    const res = await fetch("/api/public/newsletters");
    if (res.ok) { this.items = await res.json(); this.loaded = true; this.onChange(); }
  }

  render() {
    const top = this.root.scrollTop;
    clear(this.root);
    this.root.append(h("p", { class: "muted small audit-intro" },
      "The weekly note from Conscious Investments. Every issue here was checked by the office and approved before it was published. Research, not investment advice."));
    if (!this.loaded) { this.root.append(h("p", { class: "empty" }, "Loading…")); return; }
    if (!this.items.length) this.root.append(h("p", { class: "empty" }, "No issues published yet."));
    for (const i of this.items) {
      const page = i.files["issue.html"];
      this.root.append(h("div", { class: "audit-card outbox s-approved" },
        h("div", { class: "approval-head" }, h("span", { class: "chip good" }, "Published"),
          h("span", { class: "chip" }, `${i.words} words`), h("span", { class: "feed-time" }, timeAgo(i.updated))),
        h("div", { class: "outbox-title" }, i.title),
        h("div", { class: "muted small" }, i.date),
        i.files["header.png"] && page ? h("a", { href: page, target: "_blank", rel: "noopener" },
          h("img", { class: "outbox-header", src: i.files["header.png"], alt: "" })) : null,
        page ? h("div", { class: "row" }, h("a", { class: "btn small-btn", href: page, target: "_blank", rel: "noopener" }, "Read the issue")) : null));
    }
    this.root.scrollTop = top;
  }
}

export class SignInPanel {
  private error = "";
  private busy = false;
  private password = "";
  constructor(private root: HTMLElement, private captain: () => string, private onChange: () => void) {}

  render() {
    if (this.root.querySelector("input") && !this.dirty) return;   // keep the caret while typing
    this.dirty = false;
    clear(this.root);
    const input = h("input", { type: "password", autocomplete: "current-password", placeholder: "Captain's password",
      "aria-label": "Captain's password", value: this.password,
      oninput: (e: Event) => { this.password = (e.target as HTMLInputElement).value; } });
    const submit = async (e: Event) => {
      e.preventDefault();
      if (this.busy || !this.password) return;
      this.busy = true; this.error = ""; this.dirty = true; this.onChange();
      const res = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: this.password }) }).catch(() => null);
      if (res?.ok) { location.reload(); return; }
      this.error = (await res?.json().catch(() => null))?.detail ?? "Couldn't reach the office. Try again.";
      this.busy = false; this.password = ""; this.dirty = true; this.onChange();
    };
    this.root.append(
      h("p", { class: "muted small audit-intro" },
        `You're watching the office as a visitor: you can see who is working and read finished work, and nothing you do here can change anything. Only ${this.captain()} signs in.`),
      h("form", { class: "audit-card signin", onsubmit: submit },
        h("label", { class: "audit-subject" }, `${this.captain()}'s sign-in`), input,
        this.error ? h("div", { class: "approval-error", role: "alert" }, this.error) : null,
        h("div", { class: "row" }, h("button", { class: "btn", type: "submit", disabled: this.busy }, this.busy ? "Signing in…" : "Sign in"))));
  }
  private dirty = true;
}
