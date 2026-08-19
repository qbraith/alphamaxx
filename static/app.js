/* AlphaMaxx client bootstrap: Chart.js canvas init + shell behaviors.
   Charts arrive as <canvas data-chart-config="…"> fragments via HTMX. */

(function () {
  "use strict";

  // ---- Chart rendering -----------------------------------------------------
  //
  // Canvases that aren't ready (CSS not applied yet, container mid-layout)
  // are retried via ONE coalesced rescan timer with a per-canvas attempt
  // budget. Never schedule per-canvas timers: navigating away while chart
  // panes are streaming in leaves detached 0x0 canvases, and unbounded
  // per-canvas retries multiply into a timer storm that freezes the tab.

  var MAX_TRIES = 40;          // ~2.4s at 60ms per rescan
  var rescanTimer = null;

  function scheduleRescan() {
    if (rescanTimer !== null) return;
    rescanTimer = setTimeout(function () {
      rescanTimer = null;
      renderCharts(document);
    }, 60);
  }

  function renderCharts(root) {
    root = root || document;
    if (!window.Chart || !root.querySelectorAll) return;
    root.querySelectorAll("canvas.chart-canvas[data-chart-config]").forEach(function (canvas) {
      if (canvas._ch) return;
      if (!canvas.isConnected) return;  // detached by a swap — abandon
      var box = canvas.getBoundingClientRect();
      if (box.width < 20 || box.height < 20) {
        canvas._chartTries = (canvas._chartTries || 0) + 1;
        if (canvas._chartTries <= MAX_TRIES) scheduleRescan();
        return;
      }
      try {
        var config = JSON.parse(canvas.dataset.chartConfig);
        canvas._ch = new Chart(canvas, config);
      } catch (e) {
        console.error("chart init failed", canvas.id, e);
      }
    });
  }

  // Destroy chart instances inside content HTMX is about to replace, so
  // period toggles and pane reloads don't leak Chart.js registrations.
  document.addEventListener("htmx:beforeSwap", function (e) {
    var target = e.detail && e.detail.target;
    if (!target || !target.querySelectorAll) return;
    target.querySelectorAll("canvas.chart-canvas").forEach(function (canvas) {
      if (canvas._ch) { canvas._ch.destroy(); canvas._ch = null; }
    });
  });

  document.addEventListener("DOMContentLoaded", function () { renderCharts(document); });
  document.addEventListener("htmx:afterSettle", function (e) {
    renderCharts((e.detail && e.detail.target) || document);
  });

  window.AlphaMaxxCharts = { renderAll: renderCharts };

  // ---- Search box ----------------------------------------------------------

  function clearSearchResults() {
    var results = document.getElementById("search-results");
    if (results) results.innerHTML = "";
  }

  function resetSearch() {
    var search = document.getElementById("ticker-search");
    // Prevent an older autocomplete response from reopening the popup after
    // the user has already selected a company.
    if (search && window.htmx) window.htmx.trigger(search, "htmx:abort");
    if (search) {
      search.value = "";
      search.blur();
    }
    clearSearchResults();
  }

  // The exact-ticker endpoint emits this only for a valid local company.
  // Invalid Enter submissions return 204 without an event and leave the
  // current page/search state untouched.
  document.addEventListener("tickerSearchAccepted", resetSearch);

  document.addEventListener("click", function (e) {
    var container = document.querySelector(".search-container");
    var selectedResult = e.target.closest && e.target.closest("a.search-result-item");
    var primaryNavigation = e.button === 0 && !e.metaKey && !e.ctrlKey &&
      !e.shiftKey && !e.altKey;
    if (selectedResult && container && container.contains(selectedResult) && primaryNavigation) {
      // Let HTMX finish handling this click before detaching its source link.
      // This also covers stock-to-stock navigation, where the shell containing
      // the search popup persists while only #page-content is replaced.
      window.setTimeout(resetSearch, 0);
      return;
    }
    if (container && !container.contains(e.target)) {
      clearSearchResults();
    }
  });

  // "/" or Cmd/Ctrl+K focuses the command-bar search from anywhere.
  document.addEventListener("keydown", function (e) {
    // Esc closes the chart detail modal (mirrors the X button / backdrop click).
    if (e.key === "Escape") {
      var chartModal = document.getElementById("chart-modal");
      if (chartModal && chartModal.style.display !== "none") {
        chartModal.style.display = "none";
        var modalBody = document.getElementById("chart-modal-body");
        if (modalBody) modalBody.innerHTML = "";
      }
    }

    var search = document.getElementById("ticker-search");
    if (!search) return;
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if ((e.key === "/" && !typing) ||
        (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey))) {
      e.preventDefault();
      search.focus();
      search.select();
    }
    if (e.key === "Escape" && document.activeElement === search) {
      search.blur();
      clearSearchResults();
    }
  });

  // ---- Nav rail active state on HTMX navigation ------------------------------

  document.addEventListener("htmx:pushedIntoHistory", function (e) {
    var path = (e.detail && e.detail.path) || "";
    var links = document.querySelectorAll(".rail-link");
    links.forEach(function (l) { l.classList.remove("active"); });
    links.forEach(function (l) {
      var href = l.getAttribute("href");
      if (href === path || (path === "/" && href === "/") ||
          (href !== "/" && path.startsWith(href))) {
        l.classList.add("active");
      }
    });
  });
})();
