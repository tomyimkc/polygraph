/* SPDX-License-Identifier: Apache-2.0 */

(function () {
  "use strict";

  function byId(id) {
    return document.getElementById(id);
  }

  function loadJson(path) {
    return fetch(path, { cache: "no-store" }).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status + " for " + path);
      return response.json();
    });
  }

  function ratio(value) {
    return Number(value).toFixed(2) + "×";
  }

  function fmt(value, digits) {
    return Number(value).toLocaleString(undefined, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function initTheme() {
    var root = document.documentElement;
    var button = byId("theme-toggle");
    var stored = null;
    try {
      stored = localStorage.getItem("polygraph-theme");
    } catch (_) {}
    if (stored === "light" || stored === "dark") root.setAttribute("data-theme", stored);

    function isDark() {
      var explicit = root.getAttribute("data-theme");
      if (explicit === "dark") return true;
      if (explicit === "light") return false;
      return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    }

    function sync() {
      var dark = isDark();
      if (button) {
        button.setAttribute("aria-pressed", String(dark));
        var icon = button.querySelector(".theme-toggle-icon");
        if (icon) icon.textContent = dark ? "☾" : "☀";
      }
    }

    if (button) {
      button.addEventListener("click", function () {
        var next = isDark() ? "light" : "dark";
        root.setAttribute("data-theme", next);
        try {
          localStorage.setItem("polygraph-theme", next);
        } catch (_) {}
        sync();
      });
    }
    sync();
  }

  function renderScale(scale) {
    var rows = scale && scale.B_finding3_cost;
    if (!Array.isArray(rows)) return;
    var broken = rows.find(function (row) { return row.id === "7B BROKEN build"; });
    var fixed = rows.find(function (row) { return row.id === "7B FIXED build"; });
    if (!broken || !fixed) return;

    var value = fixed.prefill_median / broken.prefill_median;
    byId("prefill-ratio").textContent = ratio(value);
    byId("prefill-detail").textContent =
      fmt(broken.prefill_median, 2) + " → " + fmt(fixed.prefill_median, 2) +
      " tok/s · n=" + fixed.prefill_n + " per build";
    byId("prefill-bar").style.width = Math.min(100, value / 5 * 100).toFixed(1) + "%";
  }

  function renderRollback(receipt) {
    var gate = receipt && receipt.gate;
    var metrics = gate && gate.metrics;
    if (!metrics) return;
    byId("rollback-ratio").textContent = ratio(metrics.candidateThroughputRatio);
    byId("rollback-ratio").classList.add("ratio-negative");
  }

  function renderFreshness(scale, receipt) {
    var stamps = [];
    if (scale && scale.generated_at_utc) stamps.push(scale.generated_at_utc);
    if (receipt && receipt.completedAtUtc) stamps.push(receipt.completedAtUtc);
    if (!stamps.length) return;
    stamps.sort();
    byId("data-freshness").textContent = "Receipts captured through " + stamps[stamps.length - 1] + ".";
  }

  function renderError(message) {
    var node = byId("data-error");
    node.hidden = false;
    node.textContent = "The static receipt bundle could not be loaded (" + message + "). Raw links remain available below.";
  }

  function main() {
    initTheme();
    Promise.all([
      loadJson("data/results/scale-experiment.json"),
      loadJson("data/results/receipt.json"),
    ]).then(function (docs) {
      renderScale(docs[0]);
      renderRollback(docs[1]);
      renderFreshness(docs[0], docs[1]);
    }).catch(function (error) {
      renderError(error.message);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();
