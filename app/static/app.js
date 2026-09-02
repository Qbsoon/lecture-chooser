/* lecture_chooser — logika frontendu (vanilla JS, bez zależności) */
"use strict";

const DAY_NAMES = ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota", "Niedziela"];
const DAY_SHORT = ["Pn", "Wt", "Śr", "Cz", "Pt", "Sb", "Nd"];
const CYCLE_LABELS = {
  T: "co tydzień", A: "nieparz.", B: "parz.",
  C: "tyg. 1–2", D: "tyg. 3–4",
  1: "tyg. 1", 2: "tyg. 2", 3: "tyg. 3", 4: "tyg. 4",
};
const PALETTE = [
  "#3b82f6", "#8b5cf6", "#ec4899", "#f97316", "#eab308",
  "#22c55e", "#14b8a6", "#06b6d4", "#f43f5e", "#84cc16",
];
const OBLIGATORY_COLOR = "#64748b";

/* ---------- stan ---------- */

let dataset = null;          // /api/dataset
let selected = new Set();    // wybrane zid
let week = 1;                // wyświetlany tydzień semestru (1-4)
let courseIndex = new Map(); // zid -> {offering, course, category}
let catColor = new Map();    // category.id -> kolor
let flashTimer = null;

const $ = (id) => document.getElementById(id);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") {
      el.addEventListener(k.slice(2), v);
    } else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children) {
    if (c === null || c === undefined) continue;
    el.append(c);
  }
  return el;
}

/* ---------- pomocnicze ---------- */

const fmtTime = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

function cycleMatches(cycle, w) {
  const week4 = ((w - 1) % 4) + 1;
  for (const c of String(cycle || "T").toUpperCase().split("/")) {
    const s = c.trim();
    if (s === "T") return true;
    if (s === "A" && w % 2 === 1) return true;
    if (s === "B" && w % 2 === 0) return true;
    if (s === "C" && [1, 2].includes(week4)) return true;
    if (s === "D" && [3, 4].includes(week4)) return true;
    if (["1", "2", "3", "4"].includes(s) && Number(s) === week4) return true;
  }
  return false;
}

function cyclesOverlap(a, b) {
  return [1, 2, 3, 4].some((w) => cycleMatches(a, w) && cycleMatches(b, w));
}

function cycleBadge(cycle) {
  return cycle && cycle !== "T" ? h("span", { class: "badge", title: CYCLE_LABELS[cycle] || cycle, text: cycle }) : null;
}

function offeringTimes(offering) {
  if (!offering.timetable.length) return h("div", { class: "no-time", text: "brak terminu w rozkładzie" });
  const parts = offering.timetable.map((e) => {
    const room = e.online ? "ONLINE" : (e.room || "");
    const line = h("span", { text: `${DAY_SHORT[e.day]} ${fmtTime(e.start)}–${fmtTime(e.end)}${room ? " · " + room : ""}` });
    return h("span", null, line, cycleBadge(e.cycle));
  });
  return h("div", { class: "times" }, ...parts);
}

/* ---------- indeksowanie danych ---------- */

function indexData() {
  courseIndex = new Map();
  catColor = new Map();
  let colorIdx = 0;
  for (const cat of dataset.categories) {
    catColor.set(cat.id, cat.obligatory ? OBLIGATORY_COLOR : PALETTE[colorIdx++ % PALETTE.length]);
    for (const course of cat.courses) {
      for (const part of course.parts) {
        for (const o of part.offerings) {
          courseIndex.set(o.zid, { offering: o, course, category: cat });
        }
      }
    }
  }
}

/* ---------- ocena wyboru (lokalne lustrze logiki serwera) ---------- */

function courseState(course) {
  const perPart = course.parts.map((part) => ({
    part,
    chosen: part.offerings.filter((o) => selected.has(o.zid)),
  }));
  return { active: perPart.some((p) => p.chosen.length > 0), perPart };
}

function categoryActive(cat) {
  return cat.courses.some((c) => courseState(c).active);
}

function seriesActiveCount(series) {
  return series.category_ids.filter((id) => {
    const cat = dataset.categories.find((c) => c.id === id);
    return cat && categoryActive(cat);
  }).length;
}

function localStatus() {
  const errors = [], missing = [], warnings = [];
  const progress = [];

  for (const cat of dataset.categories) {
    const states = new Map(cat.courses.map((c) => [c.id, courseState(c)]));
    const active = cat.courses.filter((c) => states.get(c.id).active);
    const catActive = active.length > 0 || cat.obligatory;

    for (const course of active) {
      for (const { part, chosen } of states.get(course.id).perPart) {
        if (chosen.length > 1) {
          errors.push(`„${course.name}”: w części „${part.kind}” wybrano więcej niż jedną grupę`);
        } else if (chosen.length === 0 && part.offerings.length > 1) {
          missing.push(`„${course.name}”: wybierz grupę zajęć „${part.kind}”`);
        }
      }
    }

    if (cat.mode === "all") {
      if (catActive) {
        for (const c of cat.courses) {
          if (!states.get(c.id).active) {
            missing.push(`[${cat.series || cat.name}] wymagany przedmiot „${c.name}”`);
          }
        }
      }
    } else if (cat.mode === "exact" && cat.required != null) {
      if (active.length > cat.required) {
        errors.push(`Kategoria „${cat.name}”: wybrano ${active.length} z dopuszczalnych ${cat.required} przedmiotów`);
      } else if (active.length < cat.required) {
        const n = cat.required - active.length;
        missing.push(`Kategoria „${cat.name}”: wybierz jeszcze ${n} ${n === 1 ? "przedmiot" : "przedmioty"}`);
      }
    }

    progress.push({
      id: cat.id, name: cat.name, mode: cat.mode,
      required: cat.required, selected: active.length, total: cat.courses.length,
    });
  }

  for (const s of dataset.series) {
    const cats = dataset.categories.filter((c) => c.series === s.name);
    const activeN = cats.filter(categoryActive).length;
    if (s.required != null) {
      if (activeN > s.required) {
        errors.push(`Seria „${s.name}”: wybrano ${activeN} z dopuszczalnych ${s.required} kategorii`);
      } else if (activeN < s.required) {
        const n = s.required - activeN;
        missing.push(`Seria „${s.name}”: wybierz jeszcze ${n} ${n === 1 ? "kategorię" : "kategorie"}`);
      }
    }
    progress.push({
      id: `series-${s.name}`, name: s.name, mode: "series",
      required: s.required, selected: activeN, total: cats.length,
    });
  }

  // kolizje godzinowe (we wszystkich tygodniach — jak na serwerze)
  const placed = [];
  for (const zid of selected) {
    const info = courseIndex.get(zid);
    if (!info) continue;
    for (const e of info.offering.timetable) placed.push({ e, name: info.course.name });
  }
  placed.sort((a, b) => a.e.day - b.e.day || a.e.start - b.e.start);
  outer:
  for (let i = 0; i < placed.length; i++) {
    const { e: e1, name: n1 } = placed[i];
    for (let j = i + 1; j < placed.length; j++) {
      const { e: e2, name: n2 } = placed[j];
      if (e2.day !== e1.day || e2.start >= e1.end) break;
      if (e1.start >= e2.end) continue;
      if (!cyclesOverlap(e1.cycle, e2.cycle)) continue;
      warnings.push(
        `Kolizja: „${n1}” (${fmtTime(e1.start)}–${fmtTime(e1.end)}) z „${n2}” (${fmtTime(e2.start)}–${fmtTime(e2.end)})`
      );
      continue outer; // jedna kolizja na parę wystarczy
    }
  }

  return {
    ok: errors.length === 0,
    complete: errors.length === 0 && missing.length === 0,
    errors, missing, warnings, progress,
  };
}

/* ---------- operacje na wyborze ---------- */

function setCourse(course, on) {
  const all = course.parts.flatMap((p) => p.offerings);
  if (on) {
    for (const o of all) {
      if (course.parts.find((p) => p.offerings.includes(o)).offerings.length === 1) {
        selected.add(o.zid); // pojedyncze części wybierają się same
      }
    }
  } else {
    for (const o of all) selected.delete(o.zid);
  }
}

function setCategory(cat, on) {
  for (const course of cat.courses) setCourse(course, on);
}

function pickOffering(part, zid) {
  for (const o of part.offerings) selected.delete(o.zid); // "radio"
  selected.add(zid);
}

/* ---------- picker ---------- */

function courseSelectable(cat) {
  return !cat.obligatory && !cat.series && cat.mode !== "all";
}

function countChip(selectedN, required, opts = {}) {
  const text = required != null ? `${selectedN}/${required}` : String(selectedN);
  const cls = ["count-chip"];
  if (required != null && selectedN > required) cls.push("over");
  else if (required != null && selectedN === required) cls.push("done");
  else if (opts.doneWhen && selectedN > 0) cls.push("done");
  return h("span", { class: cls.join(" "), text });
}

function renderCourse(cat, course) {
  const state = courseState(course);
  const selectable = courseSelectable(cat);
  const color = catColor.get(cat.id);

  const header = h("div", { class: "course-head" },
    selectable && h("input", {
      type: "checkbox",
      checked: state.active || false,
      onchange: (ev) => { setCourse(course, ev.target.checked); changed(); },
      "aria-label": course.name,
    }),
    h("span", { class: "name", text: course.name }),
    state.active && course.parts.some((p) => p.offerings.length > 1 && !p.offerings.some((o) => selected.has(o.zid)))
      ? h("span", { class: "course-chip needs", text: "wybierz grupę" })
      : (state.active ? h("span", { class: "course-chip", text: "wybrany" }) : null),
    h("span", { class: "kinds", text: course.parts.map((p) => p.kind).join(" + ") }),
  );
  header.style.borderLeft = `3px solid ${color}`;

  const rows = [];
  for (const part of course.parts) {
    const chosen = part.offerings.filter((o) => selected.has(o.zid));
    const needsGroup = state.active && chosen.length === 0 && part.offerings.length > 1;
    for (const o of part.offerings) {
      const row = h("label", {
        class: "offering" + (selected.has(o.zid) ? " on" : "") + (needsGroup ? " needs-group" : ""),
        "data-zid": o.zid,
      },
        part.offerings.length > 1
          ? h("input", {
              type: "radio",
              name: `part-${cat.id}-${course.id}-${part.kind}`,
              checked: selected.has(o.zid) || false,
              onchange: () => { pickOffering(part, o.zid); changed(); },
            })
          : null,
        h("div", { class: "body" },
          h("div", { class: "who" },
            h("strong", { text: o.group || o.kind }),
            o.group && h("span", { class: "meta-k", text: ` (${o.kind})` }),
            o.points ? h("span", { class: "meta-p", text: ` · ${o.points}` }) : null,
            o.teachers.length ? h("span", { class: "meta-t", text: ` · ${o.teachers.join(", ")}` }) : null,
          ),
          offeringTimes(o),
        ),
      );
      rows.push(row);
    }
  }

  return h("div", { class: "course", "data-course": course.id }, header, ...rows);
}

function renderCategoryCard(cat) {
  const active = categoryActive(cat);
  const head = h("div", { class: "cat-head" },
    h("span", { class: "dot", style: `background:${catColor.get(cat.id)}` }),
    h("h3", { text: cat.name }),
    h("button", {
      class: "btn btn-select" + (active ? " selected" : ""),
      type: "button",
      text: active ? "✓ wybrana" : "Wybierz",
      onclick: () => { setCategory(cat, !active); changed(); },
    }),
  );
  return h("div", { class: "cat-card" }, head,
    ...cat.courses.map((c) => renderCourse(cat, c)));
}

function renderPicker() {
  const picker = $("picker");
  picker.textContent = "";

  const obligatory = dataset.categories.find((c) => c.obligatory);
  if (obligatory) {
    const section = h("section", { class: "p-section" },
      h("h2", { text: "🔒 Przedmioty obowiązkowe" }, h("span", { class: "count-chip", text: `${obligatory.courses.length}` })),
      h("p", { class: "p-note", text: "Przedmioty przypisane na stałe — jeśli mają grupy, wybierz jedną." }),
      ...obligatory.courses.map((c) => renderCourse(obligatory, c)),
    );
    picker.append(section);
  }

  const seriesMap = new Map(); // nazwa serii -> kategorie
  for (const cat of dataset.categories) {
    if (!cat.series || cat.obligatory) continue;
    if (!seriesMap.has(cat.series)) seriesMap.set(cat.series, []);
    seriesMap.get(cat.series).push(cat);
  }

  for (const series of dataset.series) {
    const cats = seriesMap.get(series.name) || [];
    if (!cats.length) continue;
    const activeN = cats.filter(categoryActive).length;
    const summary = h("summary", null,
      h("span", { text: series.name }),
      countChip(activeN, series.required),
    );
    const body = h("div", { class: "series-body" },
      series.note ? h("p", { class: "p-note", text: series.note }) : null,
      ...cats.map(renderCategoryCard),
    );
    picker.append(h("details", { class: "series-box p-section", open: activeN > 0 }, summary, body));
  }

  for (const cat of dataset.categories) {
    if (cat.obligatory || cat.series) continue;
    const active = cat.courses.filter((c) => courseState(c).active).length;
    const section = h("section", { class: "p-section" },
      h("h2", null,
        h("span", { class: "dot", style: `background:${catColor.get(cat.id)}` }),
        h("span", { text: cat.name }),
        countChip(active, cat.required),
      ),
      cat.note ? h("p", { class: "p-note", text: cat.note }) : null,
      ...cat.courses.map((c) => renderCourse(cat, c)),
    );
    picker.append(section);
  }
}

/* ---------- kalendarz ---------- */

function displayedEntries() {
  const out = [];
  for (const zid of selected) {
    const info = courseIndex.get(zid);
    if (!info) continue;
    for (const e of info.offering.timetable) {
      if (cycleMatches(e.cycle, week)) out.push({ e, ...info });
    }
  }
  return out;
}

function collidingZids() {
  const placed = [];
  for (const zid of selected) {
    const info = courseIndex.get(zid);
    if (!info) continue;
    for (const e of info.offering.timetable) placed.push({ e, zid });
  }
  placed.sort((a, b) => a.e.day - b.e.day || a.e.start - b.e.start);
  const bad = new Set();
  for (let i = 0; i < placed.length; i++) {
    for (let j = i + 1; j < placed.length; j++) {
      const { e: e1, zid: z1 } = placed[i];
      const { e: e2, zid: z2 } = placed[j];
      if (e2.day !== e1.day || e2.start >= e1.end) break;
      if (e1.start >= e2.end) continue;
      if (!cyclesOverlap(e1.cycle, e2.cycle)) continue;
      bad.add(z1); bad.add(z2);
    }
  }
  return bad;
}

function renderCalendar() {
  const head = $("calHead"), body = $("calBody"), inner = $("calInner");
  head.textContent = "";
  body.textContent = "";

  const entries = displayedEntries();
  const daysUsed = [...new Set(entries.map((x) => x.e.day))].sort((a, b) => a - b);
  const days = daysUsed.length ? daysUsed : [0, 1, 2, 3, 4];

  let minH = 8 * 60, maxH = 20 * 60;
  if (entries.length) {
    minH = Math.min(...entries.map((x) => x.e.start));
    maxH = Math.max(...entries.map((x) => x.e.end));
    minH = Math.floor(minH / 60) * 60;
    maxH = Math.ceil(maxH / 60) * 60;
  }
  const hours = (maxH - minH) / 60;

  inner.style.setProperty("--cal-cols", `44px repeat(${days.length}, 1fr)`);
  body.style.setProperty("--hours", hours);

  head.append(h("div", { class: "cal-corner" }));
  for (const d of days) head.append(h("div", { class: "cal-day-name", text: DAY_NAMES[d] }));

  body.append(h("div", { class: "cal-hours" }));
  for (let m = minH; m <= maxH; m += 60) {
    body.firstChild.append(h("span", {
      text: `${String(m / 60).padStart(2, "0")}:00`,
      style: `top:${((m - minH) / (maxH - minH)) * 100}%`,
    }));
  }
  const bad = collidingZids();
  const columns = new Map(days.map((d) => [d, []]));
  const laneCount = new Map(days.map((d) => [d, 0]));

  for (const d of days) {
    const dayEntries = entries
      .filter((x) => x.e.day === d)
      .sort((a, b) => a.e.start - b.e.start);
    const col = columns.get(d);
    const lanes = []; // koniec ostatniego bloku w pasie
    for (const x of dayEntries) {
      let lane = lanes.findIndex((end) => x.e.start >= end);
      if (lane === -1) { lane = lanes.length; lanes.push(0); }
      lanes[lane] = Math.max(lanes[lane], x.e.end);
      col.push({ x, lane });
    }
    laneCount.set(d, Math.max(1, lanes.length));
  }

  const maxLanes = Math.max(1, ...[...laneCount.values()]);

  for (const d of days) {
    const dayEl = h("div", { class: "cal-day" });
    for (const { x, lane } of columns.get(d)) {
      const { e, course, offering, category } = x;
      const color = catColor.get(category.id);
      const top = ((e.start - minH) / (maxH - minH)) * 100;
      const height = Math.max(2.2, ((e.end - e.start) / (maxH - minH)) * 100);
      const left = (lane / maxLanes) * 100;
      const width = (1 / maxLanes) * 100;
      const block = h("div", {
        class: "cal-block" + (bad.has(offering.zid) ? " collide" : ""),
        style: `top:${top}%;height:${height}%;left:${left}%;width:${width}%;` +
               `border-color:${color};background:${color}22;color:${color};` +
               `--block-fg:${color}`,
        title: `${course.name} — ${offering.group || offering.kind}`,
        onclick: () => flashCourse(course.id),
      },
        h("b", { text: course.name }),
        h("div", { class: "meta" },
          h("span", { text: `${offering.group || offering.kind}` }),
          e.online
            ? h("span", { class: "badge", text: "online" })
            : (e.room ? h("span", { class: "badge", text: e.room }) : null),
          cycleBadge(e.cycle),
        ),
        h("div", { class: "meta teacher", text: `${fmtTime(e.start)}–${fmtTime(e.end)} · ${e.teacher || ""}` }),
      );
      dayEl.append(block);
    }
    if (!columns.get(d).length) {
      dayEl.append(h("div", { class: "cal-empty", text: "brak zajęć" }));
    }
    body.append(dayEl);
  }
}

function flashCourse(courseId) {
  const el = document.querySelector(`.course[data-course="${courseId}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("flash");
  void el.offsetWidth; // restart animacji
  el.classList.add("flash");
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => el.classList.remove("flash"), 1300);
}

/* ---------- status / panel / toast ---------- */

function renderWeekSwitch() {
  const box = $("weekSwitch");
  box.textContent = "";
  for (let w = 1; w <= 4; w++) {
    const parity = w % 2 === 1 ? "nieparz." : "parz.";
    box.append(h("button", {
      type: "button",
      "aria-pressed": String(w === week),
      onclick: () => { week = w; renderCalendar(); renderWeekSwitch(); scheduleSave(); },
    }, h("span", { text: `Tydzień ${w}` }), h("small", { text: parity })));
  }
}

function renderStatus(status) {
  const chip = $("statusChip");
  if (status.errors.length) {
    chip.className = "status-chip err";
    chip.textContent = `Wybór nieprawidłowy (${status.errors.length})`;
  } else if (!status.complete) {
    chip.className = "status-chip warn";
    chip.textContent = "Plan niekompletny";
  } else if (status.warnings.length) {
    chip.className = "status-chip warn";
    chip.textContent = `Kompletny · ${status.warnings.length} kolizji`;
  } else {
    chip.className = "status-chip ok";
    chip.textContent = "Plan kompletny ✓";
  }

  const panel = $("statusPanel");
  const lines = [
    ...status.errors.map((t) => ["error", t]),
    ...status.warnings.map((t) => ["warning", `⚠ ${t}`]),
    ...status.missing.map((t) => ["missing", t]),
  ];
  if (status.complete && !status.warnings.length) {
    lines.unshift(["info", "Plan kompletny — wszystkie wymogi spełnione."]);
  }
  panel.textContent = "";
  for (const [cls, text] of lines) panel.append(h("div", { class: `status-line ${cls}`, text }));
  panel.hidden = lines.length === 0;
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { t.hidden = true; }, 5000);
}

/* ---------- render i zapis ---------- */

function render() {
  renderPicker();
  renderCalendar();
  renderWeekSwitch();
  renderStatus(localStatus());
}

let saveTimer = null;
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 250);
}

function changed() {
  render();
  scheduleSave();
}

async function save() {
  try {
    const res = await fetch("/api/selection", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected: [...selected], week }),
    });
    const data = await res.json();
    if (res.status === 409) {
      toast("Nie zapisano — wybór narusza limity: " + data.status.errors.join(" · "));
      renderStatus(data.status);
      return;
    }
    if (!res.ok) {
      toast("Błąd zapisu wyboru.");
      return;
    }
    renderStatus(data.status);
  } catch (err) {
    toast("Brak połączenia z serwerem — wybór nie został zapisany.");
  }
}

async function loadSelection() {
  try {
    const res = await fetch("/api/selection");
    const data = await res.json();
    selected = new Set(data.selected);
    week = data.week || 1;
  } catch (err) {
    selected = new Set();
  }
}

/* ---------- start ---------- */

async function init() {
  try {
    const res = await fetch("/api/dataset");
    if (!res.ok) throw new Error("dataset");
    dataset = await res.json();
  } catch (err) {
    $("picker").append(h("p", { class: "p-note", text: "Nie udało się wczytać danych (plan studiów / rozkład)." }));
    return;
  }
  indexData();
  await loadSelection();
  render();

  $("resetBtn").addEventListener("click", async () => {
    try {
      await fetch("/api/selection", { method: "DELETE" });
      selected = new Set();
      week = 1;
      render();
    } catch (err) {
      toast("Brak połączenia z serwerem.");
    }
  });
}

document.addEventListener("DOMContentLoaded", init);
