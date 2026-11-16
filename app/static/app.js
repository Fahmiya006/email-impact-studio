(function () {
  "use strict";
  var csrf = (document.querySelector('meta[name="csrf"]') || {}).content || "";

  function formData(form) {
    var out = {};
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.name) return;
      out[el.name] = el.type === "checkbox" ? el.checked : el.value;
    });
    return out;
  }

  document.querySelectorAll("form[data-api]").forEach(function (form) {
    var btn = form.querySelector("button[type=submit]");
    var errBox = form.querySelector("#formError");
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      errBox.hidden = true;
      var idle = btn.textContent;
      btn.textContent = "Working...";
      btn.disabled = true;
      fetch(form.getAttribute("data-api"), {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        credentials: "same-origin",
        body: JSON.stringify(formData(form))
      }).then(function (r) {
        return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; });
      }).then(function (res) {
        if (!res.ok) { throw new Error(res.body.error || "Something went wrong."); }
        var box = document.getElementById("result");
        box.innerHTML = res.body.html;
        var note = document.createElement("div");
        note.className = "hint";
        note.innerHTML = 'Saved to your history. <a href="/report/' + res.body.id + '">Open report</a> · <a href="/report/' + res.body.id + '/download">Download report</a>';
        box.insertBefore(note, box.firstChild);
        if (box.getBoundingClientRect().top > window.innerHeight * 0.6) { box.scrollIntoView({ behavior: "smooth", block: "start" }); }
      }).catch(function (e) {
        errBox.textContent = e.message || "Could not reach the server.";
        errBox.hidden = false;
      }).then(function () { btn.disabled = false; btn.textContent = idle; });
    });
  });

  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (ev) {
      if (!window.confirm(f.getAttribute("data-confirm"))) ev.preventDefault();
    });
  });
})();
