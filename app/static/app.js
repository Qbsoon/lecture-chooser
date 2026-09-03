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

/* ---------- motyw kolorystyczny ---------- */

const THEMES = ["auto", "light", "dark", "forest", "violet", "rose"];

function applyTheme(theme) {
  if (THEMES.includes(theme) && theme !== "auto") {
    document.documentElement.dataset.theme = theme;
  } else {
    delete document.documentElement.dataset.theme;
  }
}

function storedTheme() {
  try { return localStorage.getItem("theme"); } catch (err) { return null; }
}

// stosujemy zapisany motyw od razu (przed renderem), żeby nie mignęły kolory domyślne
applyTheme(storedTheme());

/* ---------- stan ---------- */

let dataset = null;          // /api/dataset
let selected = new Set();    // wybrane zid
let week = 1;                // ostatnio wybrany konkretny tydzień semestru (zapisywany na serwerze)
let view = "sum";            // widok kalendarza: "sum" (domyślny) | "A" | "B" | "w1".."w4" (tylko UI)
let hoverZid = null;          // offering pod kursorem: podświetlenie, gdy wybrany (tylko UI)
let hoverZids = new Set();    // zidy do podglądu w kalendarzu (przygaszone kafelki, tylko UI)
let openBoxes = new Set();    // rozwinięte sekcje listy (id boxa) — przetrwają re-render listy
let courseIndex = new Map(); // zid -> {offering, course, category}
let catColor = new Map();    // category.id -> kolor
let semesterStart = "2026-10-05"; // poniedziałek 1. tygodnia (daty w linkach Google Calendar)
let semesterWeeks = 15;           // liczba tygodni semestru
let flashTimer = null;

const $ = (id) => document.getElementById(id);

function h(tag, attrs = {}, ...children) {
  if (attrs === null || attrs === undefined) attrs = {};
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
    // false / "" nie są celowymi potomkami — powstają z warunków typu
    // `selectable && h(...)`. append() zrobiłby z nich tekst ("false").
    if (c === null || c === undefined || c === false || c === "") continue;
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
    const bits = [];
    if (e.online) bits.push("ONLINE");
    else if (e.room) bits.push(e.room);
    if (e.hybrid) bits.push("hybrydowe");
    const suffix = bits.length ? " · " + bits.join(" · ") : "";
    const line = h("span", { text: `${DAY_SHORT[e.day]} ${fmtTime(e.start)}–${fmtTime(e.end)}${suffix}` });
    return h("span", {}, line, cycleBadge(e.cycle));
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
  if (zid !== null && zid !== undefined) selected.add(zid); // zid = null -> odznacz wszystko w części
}

function setHover(zid) {
  if (hoverZid === zid && hoverZids.size === (zid === null ? 0 : 1)) return;
  hoverZid = zid;
  hoverZids = new Set(zid === null ? [] : [zid]);
  renderCalendar();
}

// Kurs, w którym każdy offering jest obowiązkowy (każda część ma dokładnie jedną
// pozycję — np. pracownia + seminarium bez wybierania grup). Najechanie na taki
// kurs szkicuje w kalendarzu wszystkie jego terminy naraz. Gdy wewnątrz są opcje
// do wyboru (część z wieloma offeringami), podgląd całego kursu nie ma sensu.
function coursePreviewZids(course) {
  if (!course.parts.length || course.parts.some((p) => p.offerings.length > 1)) return [];
  return course.parts.flatMap((p) => p.offerings.map((o) => o.zid).filter((z) => !selected.has(z)));
}

function setHoverCourse(course) {
  hoverZid = null; // brak pojedynczego podświetlenia
  hoverZids = new Set(coursePreviewZids(course));
  renderCalendar();
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

  const header = h("div", {
    class: "course-head",
    onmouseenter: () => setHoverCourse(course),
    onmouseleave: () => setHover(null),
  },
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
        onmouseenter: () => setHover(o.zid),
        onmouseleave: () => setHover(null),
      },
        part.offerings.length > 1
          ? h("input", {
              type: "radio",
              name: `part-${cat.id}-${course.id}-${part.kind}`,
              checked: selected.has(o.zid) || false,
              // klik na zaznaczone radio odznacza je (radio nie robi tego natywnie)
              onmousedown: (ev) => { ev.target.wasChecked = ev.target.checked; },
              onclick: (ev) => {
                pickOffering(part, ev.target.wasChecked ? null : o.zid);
                changed();
              },
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
  // kursor opuszcza listę -> czyścimy podgląd w kalendarzu
  picker.onmouseleave = () => setHover(null);

  // Wspólny zwijany box sekcji; stan rozwinięcia zapamiętujemy w openBoxes,
  // żeby przerysowanie listy po wyborze nie zwijało boxa, który użytkownik otworzył.
  const box = (id, headChildren, bodyChildren) => h("details", {
    class: "coll-box",
    open: openBoxes.has(id) || undefined,
    ontoggle: (ev) => {
      if (ev.target.open) openBoxes.add(id);
      else openBoxes.delete(id);
    },
  },
    h("summary", {}, ...headChildren),
    h("div", { class: "coll-body" }, ...bodyChildren),
  );

  const obligatory = dataset.categories.find((c) => c.obligatory);
  if (obligatory) {
    picker.append(box("obligatory",
      [
        h("span", { text: "🔒 Przedmioty obowiązkowe" }),
        h("span", { class: "count-chip", text: `${obligatory.courses.length}` }),
      ],
      [
        h("p", { class: "p-note", text: "Przedmioty przypisane na stałe — jeśli mają grupy, wybierz jedną." }),
        ...obligatory.courses.map((c) => renderCourse(obligatory, c)),
      ],
    ));
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
    picker.append(box(`series:${series.name}`,
      [
        h("span", { text: series.name }),
        countChip(activeN, series.required),
      ],
      [
        series.note ? h("p", { class: "p-note", text: series.note }) : null,
        ...cats.map(renderCategoryCard),
      ],
    ));
  }

  for (const cat of dataset.categories) {
    if (cat.obligatory || cat.series) continue;
    const active = cat.courses.filter((c) => courseState(c).active).length;
    picker.append(box(`cat:${cat.id}`,
      [
        h("span", { class: "dot", style: `background:${catColor.get(cat.id)}` }),
        h("span", { text: cat.name }),
        countChip(active, cat.required),
      ],
      [
        cat.note ? h("p", { class: "p-note", text: cat.note }) : null,
        ...cat.courses.map((c) => renderCourse(cat, c)),
      ],
    ));
  }
}

/* ---------- kalendarz ---------- */

// Czy wpis o danym cyklu pasuje do aktualnego widoku.
// "A"/"B" to widoki parzystości: pokazują zajęcia odbywające się w którymkolwiek
// tygodniu nieparzystym (1, 3) / parzystym (2, 4) semestru.
function viewMatches(cycle) {
  switch (view) {
    case "sum": return true;
    case "A": return cycleMatches(cycle, 1) || cycleMatches(cycle, 3);
    case "B": return cycleMatches(cycle, 2) || cycleMatches(cycle, 4);
    default: return cycleMatches(cycle, Number(view.slice(1)));
  }
}

function displayedEntries() {
  const out = [];
  for (const zid of selected) {
    const info = courseIndex.get(zid);
    if (!info) continue;
    for (const e of info.offering.timetable) {
      if (viewMatches(e.cycle)) out.push({ e, ...info });
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

  // Podgląd offeringów pod kursorem (niewybranych) — te same kafelki co po
  // zaznaczeniu, ale w przygaszonym stylu (klasa .preview).
  for (const zid of hoverZids) {
    if (selected.has(zid)) continue;
    const info = courseIndex.get(zid);
    if (!info) continue;
    for (const e of info.offering.timetable) {
      if (viewMatches(e.cycle)) entries.push({ e, ...info, preview: true });
    }
  }

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
        class: "cal-block" + (x.preview ? " preview" : "") + (hoverZid === offering.zid ? " hover" : "") + (bad.has(offering.zid) ? " collide" : ""),
        style: `top:${top}%;height:${height}%;left:${left}%;width:${width}%;` +
               `border-color:${color};color:${color};` +
               (x.preview ? "" : `background:${color}22;`) +
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
          e.hybrid ? h("span", { class: "badge hybrid", text: "hybrydowe" }) : null,
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
  // rozwijamy wszystkie zagnieżdżone boxy, w których leży kurs
  let box = el.closest("details");
  while (box) {
    box.open = true;
    box = box.parentElement ? box.parentElement.closest("details") : null;
  }
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
  const btn = (id, label, small, title, onclick) => box.append(h("button", {
    type: "button",
    "aria-pressed": String(view === id),
    title,
    onclick,
  }, h("span", { text: label }), h("small", { text: small })));

  btn("sum", "Sumarycznie", "1–4", "Wszystkie wybrane zajęcia — niezależnie od cyklu",
    () => { view = "sum"; renderCalendar(); renderWeekSwitch(); });
  btn("A", "A", "nieparz.", "Zajęcia odbywające się w nieparzystych tygodniach semestru",
    () => { view = "A"; renderCalendar(); renderWeekSwitch(); });
  btn("B", "B", "parz.", "Zajęcia odbywające się w parzystych tygodniach semestru",
    () => { view = "B"; renderCalendar(); renderWeekSwitch(); });
  for (let w = 1; w <= 4; w++) {
    const parity = w % 2 === 1 ? "nieparz." : "parz.";
    btn("w" + w, `Tydzień ${w}`, parity, `Konkretny tydzień ${w} semestru`,
      () => { week = w; view = "w" + w; renderCalendar(); renderWeekSwitch(); scheduleSave(); });
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

/* ---------- eksporty (menu „Pobierz”) ---------- */

function viewLabel() {
  if (view === "sum") return "sumarycznie (tygodnie 1–4)";
  if (view === "A") return "tygodnie nieparzyste";
  if (view === "B") return "tygodnie parzyste";
  return `tydzień ${view.slice(1)}`;
}

function downloadFile(name, data, mime) {
  const blob = data instanceof Blob ? data : new Blob([data], { type: `${mime}; charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// wszystkie terminy wybranych offeringów (bez filtra widoku kalendarza)
function selectedEntries() {
  const out = [];
  for (const zid of selected) {
    const info = courseIndex.get(zid);
    if (!info) continue;
    out.push({ ...info, entries: info.offering.timetable });
  }
  return out;
}

function exportCSV() {
  const esc = (v) => {
    const s = String(v ?? "");
    return /[";\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const rows = [["Przedmiot", "Typ", "Grupa", "Dzień", "Od", "Do", "Cykl", "Miejsce", "Prowadzący"]];
  for (const { offering: o, course, entries } of selectedEntries()) {
    if (!entries.length) {
      rows.push([course.name, o.kind, o.group || "", "—", "", "", "", "", o.teachers.join(", ")]);
    }
    for (const e of entries) {
      rows.push([
        course.name, o.kind, o.group || "", DAY_NAMES[e.day], fmtTime(e.start), fmtTime(e.end),
        CYCLE_LABELS[e.cycle] || e.cycle,
        [e.online ? "ONLINE" : e.room, e.hybrid ? "hybrydowe" : null].filter(Boolean).join(" ") || "",
        e.teacher || o.teachers.join(", "),
      ]);
    }
  }
  // BOM + separator „;” — żeby polski Excel otwierał UTF-8 od razu
  downloadFile("plan-zajec.csv", "\uFEFF" + rows.map((r) => r.map(esc).join(";")).join("\r\n"), "text/csv");
}

function exportJSON() {
  const plan = selectedEntries().map(({ offering: o, course, category, entries }) => ({
    zid: o.zid, course: course.name, category: category.name,
    kind: o.kind, group: o.group, teachers: o.teachers,
    timetable: entries.map((e) => ({
      day: DAY_NAMES[e.day], start: fmtTime(e.start), end: fmtTime(e.end),
      cycle: e.cycle, room: e.room, online: e.online, hybrid: e.hybrid, teacher: e.teacher,
    })),
  }));
  downloadFile("wybor-planu.json", JSON.stringify({
    selected: [...selected], week, view,
    exported_at: new Date().toISOString(),
    plan,
  }, null, 2), "application/json");
}

/* ---------- Google Calendar: dodanie zajęć jako wydarzeń ---------- */

function gcalLinks() {
  // Po jednym linku „dodaj wydarzenie” na każdy termin wybranych zajęć.
  // Link otwiera formularz nowego wydarzenia wypełniony danymi zajęć —
  // wydarzenie trafia do WŁASNEGO kalendarza użytkownika. Nic nie synchronizujemy:
  // wybór żyje tylko w ciasteczku, więc żadnej subskrypcji kalendarza nie ma.
  const links = [];
  const base = new Date(`${semesterStart}T00:00:00`);
  const p2 = (n) => String(n).padStart(2, "0");
  for (const { offering: o, course, category, entries } of selectedEntries()) {
    for (const e of entries) {
      // tygodnie semestru, w których odbywają się te zajęcia (wg cyklu)
      const weeks = [];
      for (let w = 1; w <= semesterWeeks; w++) if (cycleMatches(e.cycle, w)) weeks.push(w);
      if (!weeks.length) continue;
      const day = new Date(base);
      day.setDate(day.getDate() + (weeks[0] - 1) * 7 + e.day);
      const stamp = (m) =>
        `${day.getFullYear()}${p2(day.getMonth() + 1)}${p2(day.getDate())}` +
        `T${p2(Math.floor(m / 60))}${p2(m % 60)}00`;

      // Powtarzanie: gdy tygodnie tworzą prostą progresję co 1/2/4 tyg.,
      // dokładamy RRULE (Google nie dokumentuje parametru recur — best effort;
      // szczegóły cyklu są też w opisie wydarzenia).
      let recur = "";
      for (const step of [1, 2, 4]) {
        const expected = [];
        for (let w = weeks[0]; w <= semesterWeeks; w += step) expected.push(w);
        if (expected.length === weeks.length && expected.every((w, i) => w === weeks[i])) {
          recur = step === 1
            ? `FREQ=WEEKLY;COUNT=${weeks.length}`
            : `FREQ=WEEKLY;INTERVAL=${step};COUNT=${weeks.length}`;
          break;
        }
      }

      const params = new URLSearchParams({
        action: "TEMPLATE",
        text: `${course.name} (${o.group || o.kind})`,
        dates: `${stamp(e.start)}/${stamp(e.end)}`,
        location: [e.online ? "ONLINE" : e.room, e.hybrid ? "hybrydowe" : null].filter(Boolean).join(" ") || "—",
        details: [
          `${o.kind} · ${category.name}`,
          e.teacher || o.teachers.join(", "),
          `cykl: ${CYCLE_LABELS[e.cycle] || e.cycle} — zajęcia powtarzają się wg tego cyklu do końca semestru`,
        ].filter(Boolean).join("\n"),
        ctz: "Europe/Warsaw",
      });
      if (recur) params.set("recur", `RRULE:${recur}`);
      links.push({
        label: `${DAY_SHORT[e.day]} ${fmtTime(e.start)}–${fmtTime(e.end)} · ${course.name}${o.group ? ` (${o.group})` : ""}`,
        href: `https://calendar.google.com/calendar/render?${params}`,
      });
    }
  }
  return links;
}

function renderGcalList() {
  const list = $("gcalList");
  if (!list) return;
  list.textContent = "";
  const links = gcalLinks();
  if (!links.length) {
    list.append(h("div", { class: "gcal-empty", text: selected.size ? "Wybrane zajęcia nie mają terminów w rozkładzie." : "Najpierw wybierz zajęcia." }));
    return;
  }
  list.append(h("div", { class: "gcal-note", text: "Każdy link otwiera formularz nowego wydarzenia w Twoim kalendarzu Google:" }));
  for (const { label, href } of links) {
    list.append(h("a", { href, target: "_blank", rel: "noopener", text: label }));
  }
}

function hexToRgba(hex, alpha) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ""));
  if (!m) return `rgb(0 0 0 / ${alpha})`;
  const n = parseInt(m[1], 16);
  return `rgb(${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255} / ${alpha})`;
}

function wrapLines(ctx, text, maxW) {
  const words = String(text || "").split(/\s+/).filter(Boolean);
  const lines = [];
  let line = "";
  for (const w of words) {
    const probe = line ? `${line} ${w}` : w;
    if (ctx.measureText(probe).width <= maxW || !line) line = probe;
    else { lines.push(line); line = w; }
  }
  if (line) lines.push(line);
  return lines;
}

function drawCalendarCanvas() {
  // Zapasowy szkic kalendarza rysowany „ręcznie” na canvasie — używany
  // TYLKO, gdy snapshot DOM (domToCanvas) zawiedzie (np. Safari).
  const entries = displayedEntries();
  const daysUsed = [...new Set(entries.map((x) => x.e.day))].sort((a, b) => a - b);
  const days = daysUsed.length ? daysUsed : [0, 1, 2, 3, 4];
  let minH = 8 * 60, maxH = 20 * 60;
  if (entries.length) {
    minH = Math.floor(Math.min(...entries.map((x) => x.e.start)) / 60) * 60;
    maxH = Math.ceil(Math.max(...entries.map((x) => x.e.end)) / 60) * 60;
  }

  const SCALE = 2;     // 2x — ostrzejszy tekst na zwykłych ekranach
  const TIME_W = 54;   // kolumna godzin
  const DAY_W = 250;   // szerokość kolumny dnia
  const HEAD_H = 46;   // pasek nazw dni
  const TITLE_H = 62;  // pasek tytułu
  const HOUR_H = 80;   // wysokość jednej godziny
  const W = TIME_W + days.length * DAY_W;
  const H = TITLE_H + HEAD_H + ((maxH - minH) / 60) * HOUR_H;
  const gridY = TITLE_H + HEAD_H;
  const yAt = (m) => gridY + ((m - minH) / (maxH - minH)) * (H - gridY);
  const dayX = new Map(days.map((d, i) => [d, TIME_W + i * DAY_W]));

  const cv = document.createElement("canvas");
  cv.width = W * SCALE;
  cv.height = H * SCALE;
  const ctx = cv.getContext("2d");
  ctx.scale(SCALE, SCALE);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, W, H);
  ctx.textBaseline = "top";

  // tytuł + data eksportu
  ctx.font = "700 17px system-ui, sans-serif";
  ctx.fillStyle = "#0f172a";
  ctx.fillText("Plan zajęć", 12, 12);
  ctx.font = "12px system-ui, sans-serif";
  ctx.fillStyle = "#64748b";
  ctx.textAlign = "right";
  ctx.fillText(`widok: ${viewLabel()} · ${new Date().toLocaleDateString("pl-PL")}`, W - 12, 18);
  ctx.textAlign = "left";

  // nazwy dni
  ctx.font = "700 13px system-ui, sans-serif";
  for (const d of days) {
    ctx.fillStyle = "#0f172a";
    ctx.textAlign = "center";
    ctx.fillText(DAY_NAMES[d].toUpperCase(), dayX.get(d) + DAY_W / 2, TITLE_H + 14);
  }
  ctx.textAlign = "left";

  // siatka godzin
  ctx.lineWidth = 1;
  for (let m = minH; m <= maxH; m += 60) {
    const y = Math.round(yAt(m)) + 0.5;
    ctx.strokeStyle = "#e2e8f0";
    ctx.beginPath(); ctx.moveTo(TIME_W, y); ctx.lineTo(W, y); ctx.stroke();
    ctx.fillStyle = "#64748b";
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(fmtTime(m), TIME_W - 8, y - 12);
  }
  ctx.textAlign = "left";

  // pionowe separatory + linia pod nagłówkiem
  ctx.strokeStyle = "#e2e8f0";
  for (let i = 0; i <= days.length; i++) {
    const x = Math.round(TIME_W + i * DAY_W) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, gridY); ctx.lineTo(x, H); ctx.stroke();
  }
  ctx.strokeStyle = "#cbd5e1";
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(0, gridY - 1); ctx.lineTo(W, gridY - 1); ctx.stroke();
  ctx.lineWidth = 1;

  // rozdział na pasy — identycznie jak w renderCalendar
  const columns = new Map(days.map((d) => [d, []]));
  for (const d of days) {
    const lanes = [];
    const col = columns.get(d);
    for (const x of entries.filter((v) => v.e.day === d).sort((a, b) => a.e.start - b.e.start)) {
      let lane = lanes.findIndex((end) => x.e.start >= end);
      if (lane === -1) { lane = lanes.length; lanes.push(0); }
      lanes[lane] = Math.max(lanes[lane], x.e.end);
      col.push({ x, lane });
    }
  }
  const maxLanes = Math.max(1, ...days.map((d) => new Set(columns.get(d).map((c) => c.lane)).size));

  // kafelki zajęć
  for (const d of days) {
    for (const { x, lane } of columns.get(d)) {
      const { e, course, offering, category } = x;
      const color = catColor.get(category.id);
      const bx = dayX.get(d) + (lane / maxLanes) * DAY_W + 3;
      const bw = DAY_W / maxLanes - 6;
      const by = yAt(e.start) + 2;
      const bh = Math.max(16, yAt(e.end) - yAt(e.start) - 4);

      ctx.fillStyle = hexToRgba(color, 0.15);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(bx, by, bw, bh, 7);
      else ctx.rect(bx, by, bw, bh);
      ctx.fill();
      ctx.stroke();

      ctx.save();
      ctx.beginPath(); ctx.rect(bx, by, bw, bh); ctx.clip();
      let ty = by + 5;
      const pad = 7;
      ctx.fillStyle = "#0f172a";
      ctx.font = "700 12px system-ui, sans-serif";
      for (const ln of wrapLines(ctx, course.name, bw - pad * 2).slice(0, 3)) {
        ctx.fillText(ln, bx + pad, ty);
        ty += 14;
      }
      if (bh > 48) {
        ctx.fillStyle = "#475569";
        ctx.font = "11px system-ui, sans-serif";
        const meta = [
          offering.group || offering.kind,
          e.online ? "online" : e.room,
          e.hybrid ? "hybrydowe" : null,
          e.cycle && e.cycle !== "T" ? `cykl ${e.cycle}` : null,
        ].filter(Boolean).join(" · ");
        for (const ln of wrapLines(ctx, meta, bw - pad * 2).slice(0, 2)) {
          ctx.fillText(ln, bx + pad, ty);
          ty += 13;
        }
      }
      if (bh > 68) {
        ctx.fillStyle = "#64748b";
        ctx.fillText(`${fmtTime(e.start)}–${fmtTime(e.end)}${e.teacher ? ` · ${e.teacher}` : ""}`, bx + pad, ty);
      }
      ctx.restore();
    }
  }

  return { cv };
}

/* ---------- eksport PNG/PDF: idealny obraz strony (snapshot DOM) ---------- */

function collectCssText() {
  // wszystkie zasady CSS bieżącej strony (same-origin) — trafią do snapshotu
  let css = "";
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch { continue; } // obce arkusze → pomijamy
    for (const r of rules) css += r.cssText + "\n";
  }
  return css;
}

function domToCanvas(scale = 2) {
  // Render 1:1 tego, co widać na stronie: klon nagłówka wydruku + kalendarza
  // pakujemy w SVG (<foreignObject>), przeglądarka renderuje go w <img>
  // dokładnie jak stronę (zawijanie tekstu, kafelki, kolory, legenda),
  // a obraz kopiujemy na canvas. Technika „html-to-image”, zero bibliotek.
  const inner = $("calInner"), ph = $("printHead");
  if (!inner || !ph) return Promise.reject(new Error("brak kalendarza"));

  renderPrintHead(); // świeży nagłówek: tytuł, data, legenda kategorii

  // klon poza ekranem — mierzymy realny rozmiar w px, zanim odłączymy DOM
  const bodyEl = inner.querySelector(".cal-body");
  const calC = inner.cloneNode(true);
  calC.style.zoom = ""; // zoom z wydruku tu nie obowiązuje
  calC.style.width = `${Math.ceil(inner.getBoundingClientRect().width)}px`;
  const bodyC = calC.querySelector(".cal-body");
  if (bodyC && bodyEl) {
    bodyC.style.height = `${Math.ceil(bodyEl.getBoundingClientRect().height)}px`;
  }

  const phC = ph.cloneNode(true);
  phC.style.display = "grid"; // na ekranie #printHead jest ukryty

  // eksport pokazuje wybrane zajęcia — bez efemerycznych stanów interfejsu
  calC.querySelectorAll(".cal-block.preview").forEach((b) => b.remove());
  calC.querySelectorAll(".cal-block.hover").forEach((b) => b.classList.remove("hover"));
  calC.querySelectorAll(".flash").forEach((b) => b.classList.remove("flash"));

  const wrap = document.createElement("div");
  wrap.style.cssText =
    `position:absolute;left:-99999px;top:0;width:${calC.style.width};background:#ffffff;`;
  wrap.append(phC, calC);
  document.body.append(wrap);
  const w = Math.ceil(wrap.scrollWidth), h = Math.ceil(wrap.scrollHeight);
  wrap.remove();

  // korzeń SVG: pełny CSS strony + wymuszone JASNE kolory (jak przy wydruku)
  const bf = getComputedStyle(document.body);
  const font = bf.font ||
    `${bf.fontStyle} ${bf.fontVariant} ${bf.fontWeight} ${bf.fontSize}/${bf.lineHeight} ${bf.fontFamily}`;
  const root = document.createElement("div");
  root.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
  root.style.cssText = [
    `width:${w}px`,
    "background:#ffffff",
    // reguła html,body nie działa w SVG — kopiujemy font na korzeń klona
    `font:${font}`,
    "color:#0f172a",
    // jasny motyw niezależnie od prefers-color-scheme (jak w @media print)
    "--bg:#ffffff", "--card:#ffffff", "--text:#0f172a", "--muted:#64748b",
    "--border:#dbe3ec", "--accent:#2563eb", "--accent-soft:#dbeafe",
    "--danger:#dc2626", "--danger-soft:#fee2e2", "--warn:#d97706",
    "--warn-soft:#fef3c7", "--ok:#16a34a", "--ok-soft:#dcfce7",
    "--grid-line:#e2e8f0",
  ].join(";");
  const styleEl = document.createElement("style");
  // rem-y odnoszą się do korzenia dokumentu — w SVG to <svg>, więc 15px jak html
  styleEl.textContent = collectCssText() + "\nsvg{font-size:15px}\n";
  root.append(styleEl, phC, calC);

  const svg =
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" style="font-size:15px">` +
    `<foreignObject x="0" y="0" width="${w}" height="${h}">` +
    new XMLSerializer().serializeToString(root) +
    `</foreignObject></svg>`;
  const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }));
  const img = new Image();
  return new Promise((resolve, reject) => {
    img.onload = () => {
      const cv = document.createElement("canvas");
      cv.width = Math.max(1, Math.round(w * scale));
      cv.height = Math.max(1, Math.round(h * scale));
      const ctx = cv.getContext("2d");
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, cv.width, cv.height);
      ctx.drawImage(img, 0, 0, cv.width, cv.height);
      URL.revokeObjectURL(url);
      try { ctx.getImageData(0, 0, 1, 1); } // test „splamienia” (foreignObject w Safari)
      catch (e) { reject(e); return; }
      resolve(cv);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("SVG (foreignObject) nie renderuje się w <img>"));
    };
    img.src = url;
  });
}

async function calendarCanvas() {
  try {
    return await domToCanvas(2);
  } catch (err) {
    console.warn("Snapshot DOM nie powiódł się — używam zapasowego szkicu:", err);
    return drawCalendarCanvas().cv;
  }
}

async function exportPNG() {
  const cv = await calendarCanvas();
  cv.toBlob((blob) => blob && downloadFile(`plan-${view}.png`, blob), "image/png");
}

async function exportPDF() {
  // Prawdziwy plik PDF do pobrania: idealny obraz strony (snapshot DOM 1:1)
  // osadzony w PDF jako obraz JPEG — bez zewnętrznych bibliotek.
  const cv = await calendarCanvas();
  cv.toBlob((blob) => {
    if (!blob) return;
    blob.arrayBuffer().then((buf) => {
      downloadFile("plan-zajec.pdf", buildPdf(new Uint8Array(buf), cv.width, cv.height), "application/pdf");
    });
  }, "image/jpeg", 0.92);
}

async function exportPDFFromServer() {
  // Serwerowy PDF jest wektorowy (Chromium renderuje stronę jak przy druku:
  // zaznaczalny tekst, ostrość w każdym zoomie, mały plik). Gdy serwer nie ma
  // Playwrighta/Chromium — spadamy na wersję generowaną w przeglądarce.
  const params = new URLSearchParams({ view });
  const z = [...selected].join(",");
  if (z) params.set("z", z);
  try {
    const res = await fetch(`/api/selection.pdf?${params}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    downloadFile("plan-zajec.pdf", await res.blob());
  } catch (err) {
    console.warn("Serwerowy PDF niedostępny — używam wersji przeglądarkowej:", err);
    toast("Serwerowy PDF niedostępny — generuję w przeglądarce.");
    exportPDF();
  }
}

/* ---------- udostępnianie planu linkiem ---------- */

function shareURL() {
  // Link do aktualnego planu: wybór (?z=) + widok. Bez cookies — odbiorca
  // widzi plan, ale jego własny zapisany wybór pozostaje nietknięty.
  const params = new URLSearchParams({ view });
  const z = [...selected].join(",");
  if (z) params.set("z", z);
  return `${location.origin}${location.pathname}?${params}`;
}

function copyText(text) {
  // navigator.clipboard istnieje tylko w secure context (https / localhost);
  // na self-hosted http pomagamy sobie ukrytym polem + execCommand.
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.cssText = "position:fixed;top:0;left:-9999px";
  document.body.append(ta);
  ta.focus();
  ta.select();
  try {
    return Promise.resolve(document.execCommand("copy"));
  } finally {
    ta.remove();
  }
}

async function sharePlan() {
  if (!selected.size) {
    toast("Najpierw wybierz zajęcia — inaczej link prowadzi do pustego planu.");
    return;
  }
  const url = shareURL();
  // Telefon: natywne, systemowe okno udostępniania (aplikacje, mail, itd.).
  if (navigator.share) {
    try {
      await navigator.share({ title: "Plan zajęć", text: "Mój plan zajęć", url });
      return;
    } catch (err) {
      if (err && err.name === "AbortError") return; // anulowano — nic nie robimy
      // inne błędy (np. brak https) → spadamy na kopiowanie do schowka
    }
  }
  // PC: kopiujemy link do schowka i informujemy toastem.
  try {
    await copyText(url);
    toast("Link skopiowany do schowka — możesz go komuś wysłać.");
  } catch (err) {
    toast("Nie udało się skopiować linku.");
  }
}

function buildPdf(jpeg, imgW, imgH) {
  // Minimalny, poprawny PDF 1.4: jedna strona A4 poziomo + obraz JPEG (DCTDecode).
  const pageW = 842, pageH = 595, margin = 14; // punkty (1/72 cala)
  const scale = Math.min((pageW - margin * 2) / imgW, (pageH - margin * 2) / imgH);
  const f = (v) => v.toFixed(2);
  const x = (pageW - imgW * scale) / 2, y = (pageH - imgH * scale) / 2;
  const content = `q ${f(imgW * scale)} 0 0 ${f(imgH * scale)} ${f(x)} ${f(y)} cm /Im0 Do Q`;

  const enc = new TextEncoder();
  const chunks = [];
  const offsets = [];
  let pos = 0;
  const push = (data) => {
    const bytes = typeof data === "string" ? enc.encode(data) : data;
    chunks.push(bytes);
    pos += bytes.length;
  };

  // binarny komentarz — czytniki traktują resztę pliku jako binarną
  push("%PDF-1.4\n%\u00e2\u00e3\u00cf\u00d3\n");

  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageW} ${pageH}] `
      + "/Resources << /XObject << /Im0 5 0 R >> /ProcSet [/PDF /ImageC] >> /Contents 4 0 R >>",
    `<< /Length ${enc.encode(content).length} >>\nstream\n${content}\nendstream`,
    null, // obiekt 5: obraz — dane binarne składane osobno
  ];
  objects.forEach((body, i) => {
    offsets.push(pos);
    push(`${i + 1} 0 obj\n`);
    if (body === null) {
      push(`<< /Type /XObject /Subtype /Image /Width ${imgW} /Height ${imgH} `
        + `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>\nstream\n`);
      push(jpeg);
      push("\nendstream");
    } else {
      push(body);
    }
    push("\nendobj\n");
  });

  const xrefPos = pos;
  let xref = "xref\n0 6\n0000000000 65535 f \n";
  for (const off of offsets) xref += `${String(off).padStart(10, "0")} 00000 n \n`;
  push(xref);
  push(`trailer << /Size 6 /Root 1 0 R >>\nstartxref\n${xrefPos}\n%%EOF\n`);
  return new Blob(chunks, { type: "application/pdf" });
}

function renderPrintHead() {
  const head = $("printHead");
  if (!head) return;
  // legenda: kategorie z wybranymi zajęciami
  const used = [];
  const seen = new Set();
  if (dataset) {
    for (const zid of selected) {
      const info = courseIndex.get(zid);
      if (info && !seen.has(info.category.id)) { seen.add(info.category.id); used.push(info.category); }
    }
  }
  head.textContent = "";
  head.append(
    h("div", { class: "print-title", text: "Plan zajęć" }),
    h("div", { class: "print-date", text: `widok: ${viewLabel()} · wyeksportowano ${new Date().toLocaleString("pl-PL")}` }),
    used.length ? h("div", { class: "print-legend" },
      ...used.map((cat) => h("span", {},
        h("span", { class: "dot", style: `background:${catColor.get(cat.id)}` }),
        h("span", { text: cat.name }),
      )),
    ) : null,
  );
}

function fitPrintOnePage() {
  // Cały kalendarz na JEDNEJ stronie wydruku: mierzymy naturalny rozmiar
  // i skalujemy go (zoom przelicza też layout, więc nie powstaje 2. strona).
  renderPrintHead();
  const inner = $("calInner");
  const head = $("printHead");
  if (!inner || !head) return;
  inner.style.zoom = ""; // pomiar bez wcześniejszego skalowania
  // A4 poziomo z marginesem 10 mm (277×190 mm) w px @96dpi, minus nagłówek wydruku
  const availW = (277 / 25.4) * 96;
  const availH = (190 / 25.4) * 96 - head.offsetHeight - 10;
  const s = Math.min(1, availW / inner.scrollWidth, availH / inner.scrollHeight) * 0.97;
  inner.style.zoom = s < 1 ? String(Math.max(0.2, s)) : "";
}

function resetPrintZoom() {
  const inner = $("calInner");
  if (inner) inner.style.zoom = "";
}

function initDownloadMenu() {
  const menu = $("downloadMenu");
  if (!menu) return;
  const actions = {
    ics: () => {
      const z = [...selected].join(",");
      window.location.assign("/api/selection.ics" + (z ? `?z=${encodeURIComponent(z)}` : ""));
    },
    pdf: exportPDFFromServer,
    print: () => window.print(),
    csv: exportCSV,
    json: exportJSON,
    png: exportPNG,
  };
  menu.addEventListener("click", (ev) => {
    // „Google Calendar” rozwija podmenu z linkami „dodaj wydarzenie”
    const gcalToggle = ev.target.closest(".gcal-toggle");
    if (gcalToggle) {
      const sub = gcalToggle.closest(".dropdown-sub");
      const wasOpen = sub.classList.contains("open");
      sub.classList.toggle("open");
      if (!wasOpen) renderGcalList(); // świeży stan wyboru przy każdym rozwinięciu
      return;
    }
    const btn = ev.target.closest("[data-export]");
    if (!btn) return;
    menu.removeAttribute("open");
    const fn = actions[btn.dataset.export];
    if (fn) fn();
  });
  // klik poza menu zamyka listę (i podmenu Google)
  document.addEventListener("click", (ev) => {
    if (!menu.contains(ev.target)) {
      menu.removeAttribute("open");
      const sub = menu.querySelector(".dropdown-sub");
      if (sub) sub.classList.remove("open");
    }
  });
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
  if (dataset.semester_start) semesterStart = dataset.semester_start;
  if (dataset.semester_weeks) semesterWeeks = dataset.semester_weeks;
  indexData();
  // ?z=1,5,9 — wybór przekazany linkiem (m.in. dla serwerowego renderu PDF
  // i udostępniania planu); ?view=sum|A|B|w1..w4 — widok do wyrenderowania.
  // Z URL korzysta także headless Chromium z /api/selection.pdf.
  const params = new URLSearchParams(location.search);
  const zParam = params.get("z");
  if (zParam !== null) {
    selected = new Set(
      zParam.split(",")
        .map((s) => parseInt(s, 10))
        .filter((n) => Number.isInteger(n) && courseIndex.has(n)),
    );
  } else {
    await loadSelection();
  }
  const viewParam = params.get("view");
  if (viewParam && /^(sum|A|B|w[1-4])$/.test(viewParam)) {
    view = viewParam;
    if (viewParam[0] === "w") week = +viewParam[1];
  }
  render();
  if (zParam !== null) {
    toast("Wyświetlam udostępniony plan — Twój własny wybór nie został nadpisany.");
  }

  initDownloadMenu();
  // Dopasowanie do 1 strony wydruku: beforeprint liczy wymiary jeszcze ze
  // stylami ekranowymi, matchMedia("print") — już po zastosowaniu arkusza
  // druku, więc to jego pomiar jest wiążący.
  window.addEventListener("beforeprint", fitPrintOnePage);
  window.addEventListener("afterprint", resetPrintZoom);
  const printMedia = window.matchMedia("print");
  if (printMedia.addEventListener) {
    printMedia.addEventListener("change", (e) => (e.matches ? fitPrintOnePage() : resetPrintZoom()));
  }

  $("shareBtn").addEventListener("click", sharePlan);

  const themeSelect = $("themeSelect");
  if (themeSelect) {
    themeSelect.value = storedTheme() || "auto";
    themeSelect.addEventListener("change", () => {
      applyTheme(themeSelect.value);
      try { localStorage.setItem("theme", themeSelect.value); } catch (err) {}
    });
  }

  $("resetBtn").addEventListener("click", async () => {
    try {
      await fetch("/api/selection", { method: "DELETE" });
      selected = new Set();
      week = 1;
      view = "sum";
      render();
    } catch (err) {
      toast("Brak połączenia z serwerem.");
    }
  });
}

document.addEventListener("DOMContentLoaded", init);
