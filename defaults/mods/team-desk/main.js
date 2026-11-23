import { View, div, image, svg } from "gpui-kit";
import { h_flex, v_flex, Input, InputState } from "gpui-base";
import { appHome, checking, exec, layouts, notify, props, run, theme, window } from "herdr";

const chrome = (name, size = 15) =>
  svg("mods/workspace-chrome/" + name + ".svg")
    .size(size)
    .flex_none();
const glyph = (name) =>
  name === "codex" || name === "claude"
    ? image("icons/" + name + ".svg")
        .size(14)
        .flex_none()
    : chrome("terminal", 14);
const label = (text, size, color) =>
  div()
    .text_size(size)
    .text_color(color)
    .child(String(text ?? ""));
const dot = (color, size = 6) => div().size(size).rounded_full().bg(color).flex_none();
const line = (t) => div().w_full().h(1).flex_none().bg(t.border);
const active = (a) => a.state !== "finished";
const attention = (a) =>
  active(a) && (a.state === "blocked" || a.report?.question || ["review", "decide"].includes(a.kind));
const number = (n) => Number(n).toLocaleString("en-US");
const stateText = (a) =>
  a.state === "blocked"
    ? "Needs input"
    : a.state === "working"
      ? "Working"
      : a.state === "idle"
        ? "Idle"
        : a.state === "done"
          ? "Done"
          : a.state === "finished"
            ? "Finished"
            : "Starting";
const stateColor = (t, a) =>
  a.state === "blocked" ? t.blocked : a.state === "working" ? t.working : a.state === "done" ? t.done : t.dim;

function control(t, id, title, action, icon = null, primary = false) {
  return h_flex()
    .id(id)
    .h(29)
    .px(9)
    .gap(6)
    .flex_none()
    .items_center()
    .rounded(6)
    .cursor_pointer()
    .bg(primary ? t.solid : t.raised)
    .text_color(primary ? t.solid_foreground : t.foreground)
    .text_size(12)
    .font_medium()
    .hover((s) => s.opacity(0.82))
    .on_click((_e, cx) => action(cx))
    .when(Boolean(icon), (el) => el.child(chrome(icon, 13)))
    .child(title);
}

function section(t, title, count) {
  return h_flex()
    .items_center()
    .gap(8)
    .h(32)
    .flex_none()
    .child(label(title, 14, t.foreground).font_semibold())
    .when(count !== undefined, (el) => el.child(label(count, 11, t.dim)));
}

function backToDesk() {
  const names = layouts().map((l) => (typeof l === "string" ? l : l.name));
  run("layout.switch", { name: names.includes("desk") ? "desk" : "main" });
}

function titlebar(t) {
  const mac = window()?.platform === "macos" && !window()?.fullscreen;
  return h_flex()
    .size_full()
    .pl(mac ? 80 : 15)
    .pr(14)
    .items_center()
    .gap(8)
    .bg(t.chrome)
    .border_b(1)
    .border_color(t.border)
    .child(image("frame/welcome/herdr.png").size(17).rounded(4))
    .child(label("Herdr", 12, t.foreground).font_semibold())
    .child(label("/", 12, t.dim))
    .child(label("Team Desk", 12, t.muted))
    .child(div().flex_1())
    .child(
      h_flex()
        .id("team-workspace-back")
        .h(25)
        .px(8)
        .items_center()
        .gap(6)
        .rounded(5)
        .text_color(t.muted)
        .text_size(11.5)
        .cursor_pointer()
        .hover((s) => s.bg(t.hover).text_color(t.foreground))
        .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
        .on_click(backToDesk)
        .child(chrome("terminal", 13))
        .child("Workspace"),
    );
}

async function rpc(action, data = {}) {
  const r = await exec(
    "/usr/bin/python3",
    ["-B", appHome() + "/mods/team-desk/runtime/team.py", "ui", action, JSON.stringify(data)],
    { timeout: 95000 },
  );
  if (r.code !== 0)
    throw Error((r.stderr || r.stdout || "Team Desk could not complete this action.").trim().slice(0, 400));
  return JSON.parse(r.stdout);
}

export default class TeamDesk extends View {
  init(_p, cx) {
    if (props()?.role === "title") return;
    this.page = "overview";
    this.data = { agents: [], watch: 25000 };
    this.chat = [];
    this.history = false;
    this.tickets = {};
    this.limits = null;
    this.limitTime = 0;
    this.drafts = new Map();
    this.input = InputState.new({ placeholder: "Paste a Linear ticket or write a message…" });
    this.ticketFilter = InputState.new({ placeholder: "Find a Linear issue by title or ID…" });
    this.ticketFilter.on("change", (_e, cx) => cx.notify());
    this.filter = InputState.new({ placeholder: "Find an agent or ticket…" });
    this.input.on("submit", (_e, cx) => this.submit(cx));
    this.filter.on("change", (_e, cx) => cx.notify());
    this.narrow = false;
    this.loaded = false;
    this.generation = 0;
    this.evidenceSeq = 0;
    if (checking()) return;
    this.refresh(cx);
    cx.timer.every(5000, (cx) => this.refresh(cx));
  }

  current() {
    return this.data.agents.find((a) =>
      this.page === "task" ? a.id === this.id : a.role === "coordinator" && active(a),
    );
  }

  select(page, id, cx) {
    this.drafts.set(this.page === "task" ? this.id : "master", this.input.value());
    this.page = page;
    this.id = id;
    this.generation++;
    this.history = false;
    if (page === "tickets")
      cx.spawn(async (cx) => {
        try {
          this.tickets = await rpc("tickets");
        } catch (e) {
          this.error = e.message;
        }
        cx.notify();
      });
    this.chat = [];
    this.evidence = null;
    this.evidenceSeq++;
    this.feedback = null;
    this.input.set_value(this.drafts.get(page === "task" ? id : "master") || "");
    this.detail(cx);
    cx.notify();
  }

  refresh(cx) {
    if (this.refreshing) return;
    this.refreshing = true;
    cx.spawn(async (cx) => {
      try {
        this.data = await rpc("status");
        this.loaded = true;
        this.error = null;
        const focus = this.data.focus;
        if (focus && focus.time > (this.focusTime || 0) && Date.now() / 1000 - focus.time < 45) {
          this.focusTime = focus.time;
          this.select("task", focus.id, cx);
        } else this.detail(cx);
      } catch (e) {
        this.error = String(e.message || e);
      } finally {
        this.refreshing = false;
        cx.notify();
      }
    });
  }

  detail(cx) {
    const gen = this.generation;
    const a = this.current();
    if ((this.page === "task" || this.page === "master") && a) {
      cx.spawn(async (cx) => {
        try {
          const log = await rpc("chat", { id: a.id });
          if (gen === this.generation) this.chat = log;
        } catch (e) {
          if (gen === this.generation) this.error = e.message;
        }
        cx.notify();
      });
    }
    if (this.page === "task" && !this.evidence && a?.report?.artifacts?.length)
      this.inspect(a.report.artifacts[0], 0, cx);
    if (this.page === "usage" && Date.now() - this.limitTime > 60000) {
      this.limitTime = Date.now();
      cx.spawn(async (cx) => {
        try {
          this.limits = await rpc("limits");
          this.limitError = null;
        } catch (e) {
          this.limitError = e.message;
        }
        cx.notify();
      });
    }
  }

  act(cx, fn) {
    if (this.busy) return;
    this.busy = true;
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        await fn(cx);
        this.refresh(cx);
      } catch (e) {
        notify({ title: "Team Desk", body: String(e.message || e), level: "error" });
      } finally {
        this.busy = false;
        cx.notify();
      }
    });
  }

  submit(cx) {
    const text = this.input.value().trim();
    if (!text) return;
    const task = this.page === "task",
      agent = this.current(),
      key = task ? this.id : "master";
    const gen = this.generation;
    const message = this.feedback ? this.feedback + "\n" + text : text;
    this.act(cx, async (cx) => {
      const r = task
        ? await rpc("send", { id: agent.id, text: message })
        : await rpc("master", { text, reply: Boolean(agent?.report?.question || agent?.kind === "decide") });
      this.drafts.delete(key);
      if (this.generation !== gen) return;
      if (this.input.value().trim() === text) this.input.set_value("");
      this.feedback = null;
      if (r.delegated) this.select("task", r.id, cx);
      else if (!task && this.page !== "master") this.select("master", null, cx);
      this.detail(cx);
    });
  }

  inspect(item, index, cx) {
    const id = this.id,
      seq = ++this.evidenceSeq;
    this.evidence = { item, loading: true };
    this.feedback = null;
    cx.spawn(async (cx) => {
      try {
        const data = await rpc("preview", { id, key: item.key, index });
        if (seq === this.evidenceSeq) this.evidence = { item, data };
      } catch (e) {
        if (seq === this.evidenceSeq) this.evidence = { item, error: e.message };
      }
      cx.notify();
    });
    cx.notify();
  }

  rail(t) {
    const tabs = [
      ["overview", "panel-left", "Overview"],
      ["master", "square-pen", "Master chat"],
      ["tickets", "layers", "Tickets"],
      ["usage", "git-compare", "Usage"],
    ];
    const q = this.filter.value().trim().toLowerCase();
    const rows = this.data.agents.filter(
      (a) => a.role !== "coordinator" && (a.title + " " + a.ticket + " " + a.id).toLowerCase().includes(q),
    );
    const rank = (a) => (attention(a) ? 0 : a.state === "working" ? 1 : active(a) ? 2 : 3);
    rows.sort((a, b) => rank(a) - rank(b));
    const nav = (id, icon, text, selected, go) =>
      h_flex()
        .id("nav-" + id)
        .h(33)
        .px(9)
        .gap(9)
        .items_center()
        .rounded(6)
        .cursor_pointer()
        .bg(selected ? t.selected : t.chrome)
        .text_color(selected ? t.foreground : t.muted)
        .hover((s) => s.bg(t.hover).text_color(t.foreground))
        .on_click((_e, cx) => go(cx))
        .child(icon)
        .child(div().flex_1().min_w(0).truncate().text_size(13).child(text));
    return v_flex()
      .w(this.narrow ? 175 : 223)
      .h_full()
      .min_h(0)
      .flex_none()
      .bg(t.chrome)
      .border_r(1)
      .border_color(t.border)
      .px(10)
      .pt(13)
      .pb(8)
      .children(
        tabs.map(([id, icon, name]) =>
          nav(id, chrome(icon), name, this.page === id, (cx) => this.select(id, null, cx)),
        ),
      )
      .child(div().pt(19).px(9).pb(7).text_size(11).font_semibold().text_color(t.dim).child("Agents"))
      .child(Input.new(this.filter).h(28).mb(5).px(7).text_size(11.5).rounded(5).bg(t.background))
      .child(
        v_flex()
          .id("team-rail-scroll")
          .flex_1()
          .min_h(0)
          .overflow_y_scrollbar()
          .children(
            rows.map((a) =>
              nav(a.id, dot(stateColor(t, a)), a.title, this.page === "task" && this.id === a.id, (cx) =>
                this.select("task", a.id, cx),
              ).tooltip(a.title),
            ),
          ),
      )
      .child(line(t))
      .child(
        h_flex()
          .h(33)
          .px(8)
          .gap(7)
          .items_center()
          .text_color(t.dim)
          .child(chrome("monitor", 13))
          .child(label("Local machine", 11, t.muted)),
      );
  }

  heading(t, title, sub) {
    return v_flex()
      .gap(3)
      .mb(14)
      .child(label(title, 25, t.foreground).font_semibold())
      .when(Boolean(sub), (el) => el.child(label(sub, 12, t.muted)));
  }

  agentRow(t, a, needs) {
    const note = needs
      ? a.report?.question || a.ask
      : a.report?.steps?.find((s) => s.state === "working")?.name || a.report?.solution || a.detail;
    return h_flex()
      .id("row-" + a.id)
      .w_full()
      .min_w(0)
      .px(10)
      .py(9)
      .gap(10)
      .items_center()
      .rounded(6)
      .cursor_pointer()
      .hover((s) => s.bg(t.hover))
      .on_click((_e, cx) => this.select("task", a.id, cx))
      .child(
        div()
          .size(27)
          .rounded(7)
          .bg(t.raised)
          .flex()
          .items_center()
          .justify_center()
          .text_color(t.muted)
          .child(glyph(a.provider)),
      )
      .child(
        v_flex()
          .min_w(0)
          .flex_1()
          .gap(2)
          .child(label(a.title, 13, t.foreground).font_medium().truncate())
          .when(Boolean(note), (el) => el.child(label(note, 12, t.muted).truncate().tooltip(note)))
          .when(Boolean(a.report?.steps?.length), (el) =>
            el.child(
              h_flex()
                .gap(2)
                .h(6)
                .items_center()
                .children(
                  a.report.steps.map((s) =>
                    div()
                      .w(13)
                      .h(3)
                      .rounded_full()
                      .bg({ done: t.done, working: t.working, blocked: t.blocked }[s.state] || t.border),
                  ),
                ),
            ),
          ),
      )

      .when(!this.narrow, (el) =>
        el
          .child(
            label(a.ticket || a.provider, 11, t.muted)
              .w(92)
              .truncate(),
          )
          .child(
            h_flex()
              .w(119)
              .gap(7)
              .items_center()
              .child(
                div()
                  .w(38)
                  .h(4)
                  .rounded_full()
                  .bg(t.raised)
                  .overflow_hidden()
                  .child(
                    div()
                      .h_full()
                      .w(Math.min(100, (100 * (a.tokens || 0)) / this.data.watch) + "%")
                      .bg(a.tokens >= this.data.watch ? t.blocked : t.dim),
                  ),
              )
              .child(label(a.tokens == null ? "—" : number(a.tokens), 11, t.muted)),
          ),
      )
      .child(
        h_flex()
          .w(68)
          .items_center()
          .gap(5)
          .child(dot(stateColor(t, a), 5))
          .child(
            label(
              needs
                ? a.state === "blocked"
                  ? "Needs input"
                  : a.kind === "review"
                    ? "Review"
                    : "Decision"
                : stateText(a),
              11,
              needs ? t.blocked : t.muted,
            ),
          ),
      )
      .child(chrome("chevron-right", 12).text_color(t.dim));
  }

  overview(t) {
    const all = this.data.agents.filter((a) => a.role !== "coordinator"),
      live = all.filter(active);
    const needs = live.filter(attention),
      working = live.filter((a) => a.state === "working");
    const item = (value, name, tint) =>
      h_flex()
        .items_center()
        .gap(7)
        .child(dot(tint))
        .child(label(value, 16, t.foreground).font_semibold())
        .child(label(name, 12, t.muted));
    const main = v_flex()
      .gap(6)
      .child(this.heading(t, "Team overview"))
      .child(
        h_flex()
          .items_center()
          .gap(this.narrow ? 14 : 24)
          .pb(14)
          .child(item(working.length, "working", t.working))
          .child(item(needs.length, needs.length === 1 ? "needs you" : "need you", t.blocked))
          .child(item(live.length, "live agents", t.dim)),
      )
      .child(line(t));
    if (!this.loaded) return main.child(label("Connecting to Herdr Core…", 13, t.muted).py(12));
    if (needs.length)
      main
        .child(section(t, "Needs your judgment", needs.length))
        .children(needs.map((a) => this.agentRow(t, a, true)))
        .child(line(t));
    const rest = live
      .filter((a) => !attention(a))
      .sort((a, b) => Number(b.state === "working") - Number(a.state === "working"));
    main.child(section(t, "Agents", rest.length));
    if (!this.narrow)
      main.child(
        h_flex()
          .px(10)
          .gap(10)
          .h(23)
          .items_center()
          .border_b(1)
          .border_color(t.border)
          .child(label("Task", 11, t.dim).flex_1())
          .child(label("Ticket / agent", 11, t.dim).w(92))
          .child(label("Output tokens", 11, t.dim).w(119))
          .child(label("Status", 11, t.dim).w(90)),
      );
    main.children(rest.map((a) => this.agentRow(t, a, false)));
    if (!live.length) main.child(label("Paste a ticket below to start your first task.", 13, t.muted).py(8));
    return main;
  }

  plan(t, steps) {
    const list = v_flex().gap(0).child(section(t, "Plan"));
    for (const [i, s] of steps.entries()) {
      const color = { done: t.done, working: t.working, blocked: t.blocked }[s.state] || t.dim;
      const marker =
        s.state === "done" ? chrome("check", 11).text_color(color) : dot(color, s.state === "working" ? 7 : 5);
      list.child(
        h_flex()
          .min_h(34)
          .gap(10)
          .items_center()
          .child(
            v_flex()
              .w(18)
              .h(34)
              .items_center()
              .child(
                div()
                  .w(1)
                  .h(9)
                  .bg(i ? t.border : t.background),
              )
              .child(div().h(16).flex().items_center().child(marker))
              .child(
                div()
                  .w(1)
                  .h(9)
                  .bg(i + 1 < steps.length ? t.border : t.background),
              ),
          )
          .child(label(s.name, 13, t.foreground).flex_1())
          .child(label(s.owner, 11, t.muted))
          .child(label(s.state, 11, color)),
      );
    }
    return list;
  }

  conversation(t) {
    const list = v_flex()
      .gap(10)
      .child(
        h_flex()
          .items_center()
          .child(section(t, this.history ? "Conversation" : "Latest message"))
          .child(div().flex_1())
          .when(this.chat.length > 1, (el) =>
            el.child(
              control(t, "history", this.history ? "Show latest" : "History", (cx) => {
                this.history = !this.history;
                cx.notify();
              }),
            ),
          ),
      );
    for (const m of this.history ? this.chat.slice(-30) : this.chat.slice(-1))
      list.child(
        v_flex()
          .gap(3)
          .child(label(m.who, 11, t.muted).font_semibold())
          .child(label(m.text, 13, t.foreground).whitespace_normal()),
      );
    if (!this.chat.length) list.child(label("Send a ticket or a message below.", 13, t.muted));
    return list;
  }

  evidencePane(t, a) {
    const r = a.report || {},
      group = v_flex().gap(6).child(section(t, "Evidence"));
    group.child(
      h_flex()
        .gap(5)
        .flex_wrap()
        .children(
          r.artifacts.map((item, i) =>
            control(t, "artifact-" + i, item.title, (cx) => this.inspect(item, 0, cx), "file-text"),
          ),
        ),
    );
    const selected = this.evidence;
    if (!selected?.data) return group.child(label(selected?.error || "Loading preview…", 12, t.muted));
    const { data, item } = selected;
    if (data.type === "case") {
      const row = data.row;
      group.child(label(row.case, 14, t.foreground).font_semibold().pt(8));
      if (row.source) group.child(label(row.source, 12, t.muted).whitespace_normal());
      const side = (title, value) =>
        v_flex()
          .flex_1()
          .min_w(0)
          .p(11)
          .gap(4)
          .rounded(6)
          .bg(t.raised)
          .child(label(title, 11, t.dim))
          .child(label(value, 13, t.foreground).whitespace_normal());
      group.child(h_flex().gap(7).child(side("Before", row.before)).child(side("After", row.after)));
    } else if (data.type === "diff") {
      group.child(
        v_flex()
          .bg(t.chrome)
          .rounded(6)
          .p(9)
          .children(
            data.lines.map((s) =>
              label(
                s || " ",
                12,
                s.startsWith("+") ? t.done : s.startsWith("-") ? t.blocked : t.foreground,
              ).whitespace_normal(),
            ),
          ),
      );
    } else if (data.type === "image") group.child(image(data.path).w_full().h(290));
    else
      group.child(
        control(
          t,
          "open-html",
          "Open interactive preview",
          (cx) => this.act(cx, () => rpc("preview", { id: a.id, key: item.key, open: true })),
          "monitor",
        ),
      );
    const actions = h_flex().items_center().gap(6);
    if (data.count > 1) {
      if (data.index > 0)
        actions.child(control(t, "prev-evidence", "Previous", (cx) => this.inspect(item, data.index - 1, cx)));
      actions.child(label(data.index + 1 + " / " + data.count, 11, t.muted));
      if (data.index + 1 < data.count)
        actions.child(control(t, "next-evidence", "Next", (cx) => this.inspect(item, data.index + 1, cx)));
    }
    if (active(a))
      actions.child(
        control(
          t,
          "feedback",
          "Comment",
          (cx) => {
            this.feedback =
              "Feedback on " +
              item.title +
              " (" +
              item.key.slice(0, 12) +
              ")" +
              (data.row ? " · " + data.row.case : "");
            this.input.focus();
            cx.notify();
          },
          "square-pen",
        ),
      );
    return group.child(actions);
  }

  task(t, a) {
    if (!a) return label("Loading task…", 13, t.muted);
    const r = a.report || {},
      page = v_flex()
        .gap(12)
        .child(this.heading(t, a.title, [a.ticket, a.provider, a.id].filter(Boolean).join("  ·  ")));
    const actions = h_flex().items_center().gap(6);
    if (active(a)) {
      actions.child(control(t, "peek", "Open terminal", () => run("terminal.peek", { terminal: a.id }), "terminal"));
      if (a.state === "working")
        actions.child(control(t, "stop", "Stop turn", (cx) => this.act(cx, () => rpc("stop", { id: a.id })), "square"));
      if (!r.problem && !a.role && a.state !== "blocked")
        actions.child(
          control(t, "brief", "Ask for brief", (cx) => this.act(cx, () => rpc("brief", { id: a.id })), "file-text"),
        );
    }
    for (const [i, entry] of (a.prs || "").split(",").filter(Boolean).entries()) {
      const url = entry.split("|")[0],
        num = url.match(/\/pull\/(\d+)$/)?.[1];
      if (num)
        actions.child(
          control(t, "pr-" + i, "PR #" + num, (cx) => this.act(cx, () => rpc("openPR", { url })), "branch"),
        );
    }
    page.child(actions).child(line(t));
    if (attention(a))
      page.child(
        v_flex()
          .px(12)
          .py(9)
          .gap(4)
          .border_l(2)
          .border_color(t.blocked)
          .child(label("Needs your judgment", 11, t.muted))
          .child(
            label(
              r.question || a.ask || "Open the terminal to answer the pending prompt.",
              14,
              t.foreground,
            ).whitespace_normal(),
          )
          .when(Boolean(r.decision), (el) => el.child(label(r.decision, 12, t.muted).whitespace_normal())),
      );
    for (const key of this.data.sections || ["brief", "plan", "evidence", "review"]) {
      if (key === "brief" && (r.problem || r.example || r.solution)) {
        const brief = v_flex().gap(8).child(section(t, "Brief"));
        for (const [k, title] of [
          ["problem", "Problem"],
          ["example", "Example"],
          ["solution", "Proposed fix"],
        ])
          if (r[k])
            brief.child(
              h_flex()
                .gap(10)
                .child(label(title, 12, t.muted).w(83).flex_none())
                .child(label(r[k], 13, t.foreground).flex_1().whitespace_normal()),
            );
        page.child(brief);
      } else if (key === "plan" && r.steps?.length) page.child(this.plan(t, r.steps));
      else if (key === "evidence" && r.artifacts?.length) page.child(this.evidencePane(t, a));
      else if (key === "review" && r.review)
        page.child(
          v_flex()
            .gap(3)
            .child(section(t, "Review"))
            .child(label(r.review, 13, t.foreground).whitespace_normal()),
        );
    }
    if (this.chat.length) page.child(this.conversation(t));
    return page;
  }

  ticketList(t) {
    const query = this.ticketFilter.value().trim().toLowerCase();
    const rows = Object.entries(this.tickets).filter(([id, title]) => (id + " " + title).toLowerCase().includes(query));
    const panel = v_flex()
      .gap(5)
      .child(this.heading(t, "Tickets", "Delegate a ticket or return to its worker."))
      .child(
        Input.new(this.ticketFilter)
          .h(32)
          .px(10)
          .text_size(13)
          .bg(t.chrome)
          .border_1()
          .border_color(t.input)
          .rounded(6),
      )
      .child(section(t, "Linear issues", rows.length));
    for (const [id, title] of rows.slice(0, 75)) {
      const a = this.data.agents.find((a) => active(a) && (a.ticket || "").split(",").includes(id));
      panel.child(
        h_flex()
          .gap(10)
          .py(5)
          .items_center()
          .child(label(id, 11, t.muted).w(79))
          .child(label(title, 13, t.foreground).flex_1().min_w(0).truncate().tooltip(title))
          .child(
            control(
              t,
              "ticket-" + id,
              a ? "Open" : "Delegate",
              (cx) => {
                if (a) this.select("task", a.id, cx);
                else
                  this.act(cx, async (cx) => {
                    const r = await rpc("start", { ticket: id });
                    this.select("task", r.id, cx);
                  });
              },
              a ? "chevron-right" : "plus",
            ),
          ),
      );
    }
    if (!rows.length)
      panel.child(
        label(
          query ? "No matching ticket." : "No cached Linear tickets. Paste a ticket ID in the composer to delegate it.",
          12,
          t.muted,
        ).py(8),
      );
    return panel;
  }

  usage(t) {
    const main = v_flex()
      .gap(5)
      .child(this.heading(t, "Usage", "Account limits and output tokens by session."));
    main.child(section(t, "Account limits"));
    const meter = (pct, color) =>
      div()
        .flex_1()
        .h(5)
        .rounded_full()
        .bg(t.raised)
        .overflow_hidden()
        .child(
          div()
            .h_full()
            .w(Math.min(100, Math.max(0, pct)) + "%")
            .bg(color),
        );
    let count = 0;
    for (const [provider, rows] of Object.entries(this.limits || {}))
      for (const [name, pct] of rows) {
        main.child(
          h_flex()
            .gap(10)
            .items_center()
            .h(26)
            .child(glyph(provider))
            .child(label(provider + " · " + name, 12, t.muted).w(120))
            .child(meter(pct, pct >= 90 ? t.blocked : t.accent))
            .child(label(pct + "%", 12, t.foreground).w(45)),
        );
        count++;
      }
    if (!count)
      main.child(
        label(
          this.limitError
            ? "Account limits unavailable"
            : this.limits
              ? "No account limits reported"
              : "Reading account limits…",
          12,
          t.muted,
        ),
      );
    const watch = this.data.watch;
    main
      .child(line(t).mt(12))
      .child(section(t, "Output tokens"))
      .child(label("Watch at " + number(watch) + " output tokens", 11, t.muted));
    for (const a of this.data.agents.filter(active).sort((a, b) => (b.tokens ?? -1) - (a.tokens ?? -1)))
      main.child(
        h_flex()
          .id("usage-" + a.id)
          .gap(10)
          .h(34)
          .px(5)
          .items_center()
          .rounded(5)
          .cursor_pointer()
          .hover((s) => s.bg(t.hover))
          .on_click((_e, cx) => this.select(a.role === "coordinator" ? "master" : "task", a.id, cx))
          .child(glyph(a.provider))
          .child(label(a.title, 12, t.foreground).flex_1().truncate())
          .child(
            h_flex()
              .w(this.narrow ? 60 : 125)
              .items_center()
              .child(meter((100 * (a.tokens || 0)) / watch, a.tokens >= watch ? t.blocked : t.accent)),
          )
          .child(label(a.tokens == null ? "—" : number(a.tokens), 12, a.tokens >= watch ? t.blocked : t.muted).w(67)),
      );
    return main;
  }

  render() {
    const t = theme();
    if (props()?.role === "title") return titlebar(t);
    const a = this.current();
    const main =
      this.page === "overview"
        ? this.overview(t)
        : this.page === "usage"
          ? this.usage(t)
          : this.page === "tickets"
            ? this.ticketList(t)
            : this.page === "task"
              ? this.task(t, a)
              : v_flex()
                  .gap(6)
                  .child(this.heading(t, "Master chat", "Delegate work and steer your team."))
                  .when(Boolean(a?.report?.steps?.length), (el) => el.child(this.plan(t, a.report.steps)))
                  .child(this.conversation(t));
    const pad = this.narrow ? 16 : 28;
    const body = v_flex()
      .h_full()
      .flex_1()
      .min_h(0)
      .min_w(0)
      .bg(t.background)
      .when(Boolean(this.error), (el) => el.child(label(this.error, 12, t.blocked).px(pad).py(5)))
      .child(
        v_flex()
          .id("workspace-body-" + this.page + (this.id || ""))
          .flex_1()
          .min_h(0)
          .overflow_y_scrollbar()
          .px(pad)
          .pt(21)
          .pb(18)
          .child(main.flex_none()),
      );
    if (this.page !== "usage" && (this.page !== "task" || (a && active(a)))) {
      const blocked = a?.state === "blocked";
      const recipient = this.page === "task" ? a.title : "Master chat";
      body.child(
        v_flex()
          .flex_none()
          .px(pad)
          .pt(8)
          .pb(14)
          .gap(6)
          .border_t(1)
          .border_color(t.border)
          .child(label(this.feedback || recipient, 11, t.muted).truncate())
          .child(
            blocked
              ? control(
                  t,
                  "answer-dialog",
                  "Answer in terminal",
                  () => run("terminal.peek", { terminal: a.id }),
                  "terminal",
                )
              : h_flex()
                  .items_center()
                  .gap(8)
                  .child(
                    Input.new(this.input)
                      .flex_1()
                      .min_w(0)
                      .h(34)
                      .px(10)
                      .border_1()
                      .border_color(t.input)
                      .bg(t.chrome)
                      .rounded(7)
                      .text_size(13)
                      .text_color(t.foreground),
                  )
                  .child(control(t, "send", this.busy ? "Sending…" : "Send", (cx) => this.submit(cx), null, true)),
          ),
      );
    }
    return h_flex()
      .id("team-desk-root")
      .size_full()
      .min_w(0)
      .min_h(0)
      .text_color(t.foreground)
      .on_size_change((size, cx) => {
        const next = size.width < 760;
        if (next !== this.narrow) {
          this.narrow = next;
          cx.notify();
        }
      })
      .child(this.rail(t))
      .child(body);
  }
}
