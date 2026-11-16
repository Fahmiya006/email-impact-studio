/* "Your data" page: send the chosen CSV file to the server, show its columns, let the person map them, and run the analysis.
   Everything from the file is added with textContent, never as HTML. */
(function () {
  "use strict";
  var csrf = (document.querySelector('meta[name="csrf"]') || {}).content || "";
  var fileInput = document.getElementById("fileInput"), readBtn = document.getElementById("readBtn");
  var upErr = document.getElementById("uploadError"), form = document.getElementById("mapForm"), result = document.getElementById("result");
  if (!fileInput || !form) return;
  var info = null;

  function h(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === "text") n.textContent = attrs[k]; else n.setAttribute(k, attrs[k]); });
    (kids || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }
  function showError(box, msg) { box.textContent = msg; box.hidden = false; }
  function usable(c) { return c.kind === "binary" || c.kind === "numeric"; }
  function colByName(n) { return info.columns.filter(function (c) { return c.name === n; })[0]; }

  function field(id, label, control, hint) {
    return h("div", { "class": "field" }, [h("label", { "for": id, text: label }), control, hint ? h("span", { "class": "hint", text: hint }) : null]);
  }
  function select(id, options, selected, allowNone) {
    var s = h("select", { id: id, name: id });
    if (allowNone) s.appendChild(h("option", { value: "", text: "None" }));
    options.forEach(function (o) {
      var op = h("option", { value: o.value, text: o.label });
      if (o.value === selected) op.selected = true;
      s.appendChild(op);
    });
    return s;
  }
  function checklist(name, cols, chosen, legend) {
    var fs = h("fieldset", { "class": "checks" }, [h("legend", { text: legend })]);
    cols.forEach(function (c, i) {
      var id = name + "_" + i;
      var cb = h("input", { type: "checkbox", id: id, value: c.name, "data-group": name });
      cb.checked = chosen.indexOf(c.name) >= 0;
      var lb = h("label", { "for": id, text: c.name + "  (" + c.kind + ")" });
      fs.appendChild(h("div", { "class": "field check" }, [cb, lb]));
    });
    return fs;
  }
  function checked(name) {
    return Array.prototype.map.call(form.querySelectorAll('input[data-group="' + name + '"]:checked'), function (x) { return x.value; });
  }
  function num(id) { var v = form.elements[id] && form.elements[id].value; return v === "" ? null : v; }

  function levelsFor(name) { var c = colByName(name); return c ? c.levels : []; }
  function fillControl(selected) {
    var sel = form.elements.control, lv = levelsFor(form.elements.group.value);
    sel.textContent = "";
    lv.forEach(function (l) {
      var op = h("option", { value: l[0], text: l[0] + "  (" + l[1].toLocaleString() + " customers)" });
      if (l[0] === selected) op.selected = true;
      sel.appendChild(op);
    });
  }

  function build() {
    var sg = info.suggest, groupCols = info.columns.filter(function (c) { return c.group_ok; });
    form.textContent = "";
    var head = h("h2", { text: "2. Tell the app what each column is" });
    var meta = h("span", { "class": "hint", text: info.name + ": " + info.rows.toLocaleString() + " customers, " + info.columns.length + " columns" });
    // preview of the first rows
    var th = h("tr", {}, info.columns.map(function (c) { return h("th", { text: c.name }); }));
    var body = info.sample.map(function (r) { return h("tr", {}, r.map(function (v) { return h("td", { text: v }); })); });
    var preview = h("div", { "class": "scroll" }, [h("table", { "class": "preview" }, [h("thead", {}, [th]), h("tbody", {}, body)])]);

    var grid = h("div", { "class": "formgrid" });
    grid.appendChild(field("group", "Group column", select("group", groupCols.map(function (c) { return { value: c.name, label: c.name }; }), sg.group), "Which email or treatment each customer got"));
    grid.appendChild(field("control", "Control group", h("select", { id: "control", name: "control" }), "The customers who got nothing, or the old version"));
    var outCols = info.columns.filter(usable);
    grid.appendChild(field("primary", "Main outcome", select("primary", outCols.map(function (c) { return { value: c.name, label: c.name + " (" + c.kind + ")" }; }), sg.outcomes[0]), "The result the verdict is based on"));
    var numCols = info.columns.filter(function (c) { return c.kind === "numeric"; });
    grid.appendChild(field("revenue", "Revenue column", select("revenue", numCols.map(function (c) { return { value: c.name, label: c.name }; }), sg.revenue, true), "Spend or revenue per customer; needed for profit"));
    grid.appendChild(field("pre", "Pre-period column", select("pre", info.columns.filter(usable).map(function (c) { return { value: c.name, label: c.name }; }), sg.pre, true), "Optional: spend or activity before the test, to tighten intervals"));
    var other = h("div", { "class": "full" });
    var skipId = function (c) { return !/(^id$|^id_|_id$)/i.test(c.name); };
    other.appendChild(checklist("others", outCols.filter(skipId), sg.outcomes.slice(1), "Other outcomes to measure (optional)"));
        other.appendChild(checklist("features", info.columns.filter(function (c) { return (c.kind === "category" || usable(c)) && c.distinct >= 2 && c.distinct <= 30 && skipId(c) && c.name !== sg.group && sg.outcomes.indexOf(c.name) < 0; }), sg.features, "Customer attributes, to show who responds (optional)"));
    grid.appendChild(other);
    var econ = h("div", { "class": "formgrid full" });
    [["margin_pct", "Gross margin (%)", 40, "Needed for profit", "0.1"], ["cost_per_contact", "Cost per email ($)", 0.1, "Charged to every group except the control", "0.01"],
     ["list_size", "Customers on list", 100000, "To scale profit to your list", "1"], ["fixed_cost", "Fixed cost ($)", 0, "Creative or set-up, optional", "1"]].forEach(function (f) {
      var i = h("input", { type: "number", id: f[0], name: f[0], value: String(f[2]), min: "0", step: f[4] });
      econ.appendChild(field(f[0], f[1], i, f[3]));
    });
    econ.appendChild(field("label", "Run name", h("input", { type: "text", id: "label", name: "label", maxlength: "80" }), "Optional"));
    grid.appendChild(econ);
    var btn = h("button", { type: "submit", "class": "primary", text: "Analyse data" });
    var err = h("p", { "class": "error", id: "formError", role: "alert" }); err.hidden = true;
    form.appendChild(head); form.appendChild(meta); form.appendChild(preview); form.appendChild(grid);
    form.appendChild(h("div", { "class": "actions" }, [btn])); form.appendChild(err);
    fillControl(sg.control);
    form.elements.group.addEventListener("change", function () { fillControl(null); });
    form.hidden = false;
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  readBtn.addEventListener("click", function () {
    upErr.hidden = true;
    var f = fileInput.files && fileInput.files[0];
    if (!f) { showError(upErr, "Choose a CSV file first."); return; }
    var idle = readBtn.textContent; readBtn.disabled = true; readBtn.textContent = "Reading...";
    fetch("/api/dataset", { method: "POST", credentials: "same-origin", body: f,
      headers: { "Content-Type": "text/csv", "X-CSRF-Token": csrf, "X-Filename": encodeURIComponent(f.name) } })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) { if (!res.ok) throw new Error(res.body.error || "Could not read the file."); info = res.body; result.textContent = ""; build(); })
      .catch(function (e) { showError(upErr, e.message || "Could not reach the server."); })
      .then(function () { readBtn.disabled = false; readBtn.textContent = idle; });
  });

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var err = document.getElementById("formError"); err.hidden = true;
    var btn = form.querySelector("button[type=submit]"), idle = btn.textContent;
    var primary = form.elements.primary.value, revenue = form.elements.revenue.value;
    var outcomes = [primary].concat(checked("others")).concat(revenue ? [revenue] : []);
    outcomes = outcomes.filter(function (v, i) { return v && outcomes.indexOf(v) === i; });
    var payload = { dataset_id: info.dataset_id, group: form.elements.group.value, control: form.elements.control.value, outcomes: outcomes, revenue: revenue,
      pre: form.elements.pre.value, features: checked("features"), margin_pct: revenue ? num("margin_pct") : null, cost_per_contact: revenue ? num("cost_per_contact") : null,
      list_size: num("list_size"), fixed_cost: num("fixed_cost"), label: form.elements.label.value };
    btn.disabled = true; btn.textContent = "Analysing...";
    fetch("/api/analyze", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify(payload) })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.body.error || "Something went wrong.");
        result.innerHTML = res.body.html;
        var note = h("div", { "class": "hint" });
        note.innerHTML = 'Saved to your history. <a href="/report/' + res.body.id + '">Open report</a> · <a href="/report/' + res.body.id + '/download">Download report</a>';
        result.insertBefore(note, result.firstChild);
        result.scrollIntoView({ behavior: "smooth", block: "start" });
      })
      .catch(function (e) { err.textContent = e.message || "Could not reach the server."; err.hidden = false; })
      .then(function () { btn.disabled = false; btn.textContent = idle; });
  });
})();
