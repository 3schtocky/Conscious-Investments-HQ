// Settings: edit each team member's nickname, avatar, persona and model tier.
import {
  ACCESSORIES, CLOTHES, HAIRS, HAIR_STYLES, ID_PRESET, imageOf, PRESETS, resolveParts, SKINS,
  type AvatarParts, type AvatarSpec,
} from "../office/avatar";
import type { OfficeState } from "../state";
import { avatarImage, modelLabel } from "./panels";
import { clear, h } from "./dom";


export class SettingsPanel {
  private member = "captain";
  private draft: { nickname: string; persona: string; model: string; avatar: AvatarSpec } | null = null;
  private message: { text: string; ok: boolean } | null = null;

  constructor(private root: HTMLElement, private state: OfficeState) {}

  select(id: string) { this.member = id; this.draft = null; this.message = null; this.render(); }

  render() {
    clear(this.root);
    const members = ["captain", ...this.state.agents.keys()];
    const picker = h("div", { class: "members" }, ...members.map((id) => {
      const spec = id === "captain" ? this.state.captain.avatar ?? "captain" : this.state.agents.get(id)!.avatar;
      return h("button", { class: `member${id === this.member ? " active" : ""}`, title: this.state.name(id),
        onclick: () => this.select(id) }, avatarImage(id, spec, 2), h("span", {}, this.state.name(id)));
    }));
    this.root.append(picker);

    const agent = this.state.agents.get(this.member);
    if (!this.draft) {
      this.draft = agent
        ? { nickname: agent.nickname, persona: agent.persona, model: agent.model, avatar: agent.avatar }
        : { nickname: this.state.captain.nickname, persona: "", model: "", avatar: this.state.captain.avatar ?? "captain" };
    }
    const d = this.draft;
    const form = h("div", { class: "settings-form" });
    const preview = h("div", { class: "preview" }, avatarImage(this.member, d.avatar, 7));
    form.append(h("div", { class: "preview-row" }, preview,
      h("div", {}, h("h3", {}, d.nickname || "…"), h("div", { class: "muted" }, agent ? `${agent.role}` : "The Captain"))));

    form.append(field("Nickname", h("input", { value: d.nickname, maxlength: 24,
      oninput: (e: Event) => { d.nickname = (e.target as HTMLInputElement).value; } })));

    // Avatar editor
    const parts: AvatarParts = resolveParts(imageOf(d.avatar) ? null : d.avatar, ID_PRESET[this.member]);
    const setPart = (k: keyof AvatarParts, v: string) => { d.avatar = { ...parts, [k]: v }; this.render(); };
    form.append(h("h4", {}, "Avatar"));
    form.append(field("Start from", h("select", { onchange: (e: Event) => { d.avatar = (e.target as HTMLSelectElement).value; this.render(); } },
      h("option", { value: "" }, "Choose a preset…"),
      ...Object.keys(PRESETS).map((p) => h("option", { value: p }, p[0].toUpperCase() + p.slice(1))))));
    form.append(swatches("Skin", SKINS, parts.skin, (c) => setPart("skin", c)));
    form.append(field("Hair style", select(HAIR_STYLES, parts.hairStyle, (v) => setPart("hairStyle", v))));
    form.append(swatches("Hair colour", HAIRS, parts.hair, (c) => setPart("hair", c)));
    form.append(swatches("Top", CLOTHES, parts.shirt, (c) => setPart("shirt", c)));
    form.append(swatches("Trousers", CLOTHES, parts.pants, (c) => setPart("pants", c)));
    form.append(field("Accessory", select(ACCESSORIES, parts.accessory, (v) => setPart("accessory", v))));
    const upload = h("input", { type: "file", accept: "image/png", class: "hidden",
      onchange: (e: Event) => this.upload((e.target as HTMLInputElement).files?.[0]) });
    form.append(h("div", { class: "row" },
      h("button", { class: "btn ghost", onclick: () => upload.click() }, "Upload a PNG instead…"), upload,
      imageOf(d.avatar) ? h("span", { class: "muted" }, "Using an uploaded image") : null));

    if (agent) {
      form.append(h("h4", {}, "Work style"));
      form.append(field("Persona", h("textarea", { rows: 5, maxlength: 1200,
        oninput: (e: Event) => { d.persona = (e.target as HTMLTextAreaElement).value; } }, d.persona)));
      const tiers = Object.keys(this.state.models);
      const options = tiers.includes(agent.model) ? tiers : [agent.model, ...tiers];
      form.append(field("Model", select(options, d.model, (v) => { d.model = v; },
        (v) => (this.state.models[v] ? `${v} · ${modelLabel(this.state.models[v])}` : `${v} (set in roster.yaml)`))));
      form.append(h("p", { class: "muted small" }, "Changes apply to new tasks. Work already under way keeps the profile it started with."));
    }

    form.append(h("div", { class: "row" }, h("button", { class: "btn", onclick: () => this.save() }, "Save"),
      this.message ? h("span", { class: this.message.ok ? "ok" : "err" }, this.message.text) : null));
    this.root.append(form);
  }

  private async save() {
    const d = this.draft!;
    const agent = this.state.agents.get(this.member);
    const body: Record<string, unknown> = { nickname: d.nickname.trim(), avatar: d.avatar };
    if (agent) {
      body.persona = d.persona.trim();
      if (d.model !== agent.model) body.model = d.model;   // an explicit model id stays as-is
    }
    const res = await fetch(`/api/members/${this.member}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    this.message = res.ok ? { text: "Saved.", ok: true } : { text: (await res.json()).detail ?? "Couldn't save.", ok: false };
    if (res.ok) this.draft = null;
    this.render();
  }

  private upload(file: File | undefined) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      const res = await fetch(`/api/members/${this.member}/avatar`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ data_url: reader.result }) });
      this.message = res.ok ? { text: "Avatar uploaded.", ok: true } : { text: (await res.json()).detail ?? "Upload failed.", ok: false };
      if (res.ok) this.draft = null;
      this.render();
    };
    reader.readAsDataURL(file);
  }
}

function field(label: string, control: HTMLElement): HTMLElement {
  return h("label", { class: "field" }, h("span", {}, label), control);
}

function select(values: string[], current: string, onChange: (v: string) => void, fmt = (v: string) => v): HTMLElement {
  return h("select", { onchange: (e: Event) => onChange((e.target as HTMLSelectElement).value) },
    ...values.map((v) => h("option", { value: v, selected: v === current }, fmt(v))));
}

function swatches(label: string, colors: string[], current: string, onPick: (c: string) => void): HTMLElement {
  const row = h("div", { class: "swatches" }, ...colors.map((c) => {
    const b = h("button", { class: `swatch${c.toLowerCase() === current.toLowerCase() ? " active" : ""}`, title: c,
      onclick: () => onPick(c) });
    b.style.background = c;
    return b;
  }));
  const custom = h("input", { type: "color", value: current, onchange: (e: Event) => onPick((e.target as HTMLInputElement).value) });
  return h("div", { class: "field" }, h("span", {}, label), h("div", { class: "row" }, row, custom));
}
