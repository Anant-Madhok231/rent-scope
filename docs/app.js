(function () {
  "use strict";

  const DAVIS_CENTER = [38.5449, -121.749];
  const GEOJSON_URL = new URL("rentals.geojson", window.location.href).href;
  const MARKET_URL = new URL("market_trend.json", window.location.href).href;

  let map;
  let layerGroup;
  let allFeatures = [];
  let markersById = new Map();
  let firstSummary = true;
  let marketTrend = [];
  let chartInstance = null;
  let baseLayer = null;

  function scoreColor(score) {
    const s = Number(score) || 0;
    if (s < 40) return "#5c6b7a";
    if (s < 55) return "#3d6f8f";
    if (s < 70) return "#2a9d8f";
    if (s < 85) return "#6bcf7a";
    return "#c8e850";
  }

  function formatMoney(n) {
    const x = Number(n);
    if (!Number.isFinite(x)) return "—";
    return "$" + x.toLocaleString("en-US", { maximumFractionDigits: 0 });
  }

  function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
  }

  function runTween(ms, onFrame) {
    const t0 = performance.now();
    function frame(now) {
      const u = Math.min(1, (now - t0) / ms);
      const done = u >= 1;
      onFrame(done ? 1 : easeOutCubic(u), done);
      if (!done) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  function featureId(f) {
    const p = f.properties || {};
    const c = f.geometry.coordinates || [];
    return `${p.address || ""}|${p.rent}|${c[0]}|${c[1]}`;
  }

  function chartDomId(f) {
    return "c" + featureId(f).replace(/[^a-zA-Z0-9]/g, "").slice(0, 56);
  }

  function listingHref(props) {
    const u = String(props.listing_url || "").trim();
    if (u) return u;
    return (
      "https://www.google.com/search?q=" +
      encodeURIComponent(String(props.address || "") + " Davis CA apartments for rent")
    );
  }

  function roomLabel(rt) {
    const r = String(rt || "unknown").toLowerCase();
    if (r === "shared") return "Shared room";
    if (r === "private") return "Private room";
    return "Unspecified";
  }

  function roomBadgeHtml(rt) {
    const r = String(rt || "unknown").toLowerCase();
    let cls = "room-badge-unknown";
    if (r === "private") cls = "room-badge-private";
    if (r === "shared") cls = "room-badge-shared";
    return '<span class="room-badge ' + cls + '">' + escapeHtml(roomLabel(rt)) + "</span>";
  }

  function getChartTickColor() {
    const st = getComputedStyle(document.documentElement);
    return st.getPropertyValue("--muted").trim() || "#8b96a8";
  }

  function getChartTextColor() {
    const st = getComputedStyle(document.documentElement);
    return st.getPropertyValue("--text").trim() || "#eef2f8";
  }

  function destroyActiveChart() {
    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }
  }

  function openChartFromPopup(canvas, props) {
    destroyActiveChart();
    if (!canvas || typeof Chart === "undefined" || !props) return;
    const listingTrend = Array.isArray(props.listing_rent_trend)
      ? props.listing_rent_trend
      : [];
    const city = Array.isArray(marketTrend) ? marketTrend : [];
    const yearsSet = new Set();
    city.forEach(function (x) {
      yearsSet.add(x.year);
    });
    listingTrend.forEach(function (x) {
      yearsSet.add(x.year);
    });
    const years = Array.from(yearsSet).sort(function (a, b) {
      return a - b;
    });
    const cityByY = {};
    city.forEach(function (x) {
      cityByY[x.year] = x.median_rent;
    });
    const listByY = {};
    listingTrend.forEach(function (x) {
      listByY[x.year] = x.rent_est;
    });
    const cityData = years.map(function (y) {
      return Object.prototype.hasOwnProperty.call(cityByY, y) ? cityByY[y] : null;
    });
    const listData = years.map(function (y) {
      return Object.prototype.hasOwnProperty.call(listByY, y) ? listByY[y] : null;
    });
    const tc = getChartTickColor();
    const leg = getChartTextColor();
    const root = getComputedStyle(document.documentElement);
    const c1 = root.getPropertyValue("--accent-2").trim() || "#7c9eff";
    const c2 = root.getPropertyValue("--accent").trim() || "#3ee0c2";
    chartInstance = new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: years,
        datasets: [
          {
            label: "Yolo County median gross rent (ACS B25064)",
            data: cityData,
            borderColor: c1,
            backgroundColor: "transparent",
            tension: 0.28,
            spanGaps: true,
            pointRadius: 2,
          },
          {
            label: "Listing rent scaled to city trend",
            data: listData,
            borderColor: c2,
            backgroundColor: "transparent",
            tension: 0.28,
            spanGaps: true,
            pointRadius: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            labels: {
              color: leg,
              boxWidth: 10,
              font: { size: 10 },
            },
          },
        },
        scales: {
          x: {
            ticks: { color: tc, maxRotation: 0, font: { size: 10 } },
            grid: { color: "rgba(128,128,128,0.12)" },
          },
          y: {
            ticks: {
              color: tc,
              font: { size: 10 },
              callback: function (v) {
                return "$" + v;
              },
            },
            grid: { color: "rgba(128,128,128,0.12)" },
          },
        },
      },
    });
  }

  function passesFilters(props, filters) {
    const rent = Number(props.rent);
    const beds = Number(props.beds);
    const score = Number(props.opportunity_score);
    const ptype = String(props.property_type || "");
    const rtype = String(props.room_type || "unknown").toLowerCase();

    if (filters.room != null && filters.room !== "") {
      if (rtype !== filters.room) return false;
    }

    if (filters.maxRent != null && rent > filters.maxRent) return false;
    if (filters.minScore != null && score < filters.minScore) return false;

    if (filters.beds != null) {
      if (filters.beds === 4) {
        if (beds < 4) return false;
      } else if (beds !== filters.beds) {
        return false;
      }
    }

    if (filters.propType && ptype !== filters.propType) return false;
    return true;
  }

  function readFilters() {
    const rentEl = document.getElementById("filter-rent");
    const bedsEl = document.getElementById("filter-beds");
    const scoreEl = document.getElementById("filter-score");
    const typeEl = document.getElementById("filter-type");
    const roomEl = document.getElementById("filter-room");

    const maxRentRaw = rentEl.value.trim();
    const minScoreRaw = scoreEl.value.trim();

    return {
      room: roomEl.value === "" ? null : roomEl.value,
      maxRent: maxRentRaw === "" ? null : Number(maxRentRaw),
      beds: bedsEl.value === "" ? null : Number(bedsEl.value),
      minScore: minScoreRaw === "" ? null : Number(minScoreRaw),
      propType: typeEl.value || null,
    };
  }

  function updateSummary(features) {
    const n = features.length;
    const intro = firstSummary && n > 0;
    if (intro) firstSummary = false;

    const elCount = document.getElementById("metric-count");
    const elAvg = document.getElementById("metric-avg-rent");
    const elBest = document.getElementById("metric-best");
    const elCheap = document.getElementById("metric-cheapest");

    if (!n) {
      elCount.textContent = "0";
      elAvg.textContent = "—";
      elBest.textContent = "—";
      elCheap.textContent = "—";
      return;
    }

    let rentSum = 0;
    let best = -1;
    let cheapest = Infinity;
    for (const f of features) {
      const p = f.properties;
      const r = Number(p.rent);
      rentSum += r;
      best = Math.max(best, Number(p.opportunity_score));
      cheapest = Math.min(cheapest, r);
    }

    const avg = rentSum / n;

    if (!intro) {
      elCount.textContent = n.toString();
      elAvg.textContent = formatMoney(avg);
      elBest.textContent = best.toFixed(1);
      elCheap.textContent = formatMoney(cheapest);
      return;
    }

    runTween(820, function (e, done) {
      if (done) {
        elCount.textContent = n.toString();
        elAvg.textContent = formatMoney(avg);
        elBest.textContent = best.toFixed(1);
        elCheap.textContent = formatMoney(cheapest);
        return;
      }
      elCount.textContent = Math.max(0, Math.round(n * e)).toString();
      elAvg.textContent = formatMoney(avg * e);
      elBest.textContent = (best * e).toFixed(1);
      elCheap.textContent = formatMoney(cheapest * e);
    });
  }

  function renderRanking(features) {
    const list = document.getElementById("ranking-list");
    list.innerHTML = "";

    const sorted = features
      .slice()
      .sort(function (a, b) {
        return (
          Number(b.properties.opportunity_score) -
          Number(a.properties.opportunity_score)
        );
      })
      .slice(0, 10);

    sorted.forEach(function (f, idx) {
      const p = f.properties;
      const id = featureId(f);
      const li = document.createElement("li");
      li.tabIndex = 0;
      li.dataset.fid = id;
      li.className = "rank-item";
      li.style.setProperty("--ri", String(idx));
      li.innerHTML =
        '<div class="rank-row-top">' +
        '<span class="rank-addr" title="' +
        escapeAttr(p.address) +
        '">' +
        escapeHtml(p.address) +
        roomBadgeHtml(p.room_type) +
        "</span>" +
        '<span class="rank-score">' +
        Number(p.opportunity_score).toFixed(1) +
        "</span></div>" +
        '<div class="rank-meta">' +
        formatMoney(p.rent) +
        " · " +
        p.beds +
        " bd · " +
        escapeHtml(p.property_type || "") +
        "</div>";

      const open = function () {
        focusFeature(id);
      };
      li.addEventListener("click", open);
      li.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      });
      list.appendChild(li);
    });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  function focusFeature(id) {
    const m = markersById.get(id);
    if (m) {
      map.setView(m.getLatLng(), Math.max(map.getZoom(), 15), { animate: true });
      m.openPopup();
    }
  }

  function formatStops(props) {
    const stops = props.unitrans_stops;
    if (!Array.isArray(stops) || !stops.length) {
      return "<p class=\"popup-stops-title\">No Unitrans stops in OSM within 1.5 km.</p>";
    }
    let html = '<div class="popup-stops-title">Unitrans nearby</div><ul>';
    stops.forEach(function (s) {
      const name = escapeHtml(s.name || "Stop");
      const km = Number(s.km);
      html += "<li><strong>" + name + "</strong> · " + km.toFixed(2) + " km</li>";
    });
    html += "</ul>";
    return html;
  }

  function popupHtml(f) {
    const props = f.properties;
    const cid = chartDomId(f);
    const beds = props.beds;
    const baths = props.baths;
    const dmu = Number(props.dist_km_memorial_union);
    const ds = Number(props.dist_km_silo);
    const conv = Number(props.convenience_800m_count);
    const cnear = props.convenience_nearest_km;
    let cnearTxt = "—";
    if (cnear != null && cnear !== "" && Number.isFinite(Number(cnear))) {
      cnearTxt = Number(cnear).toFixed(2) + " km";
    }
    const portal = escapeAttr(listingHref(props));
    return (
      '<div class="popup-title">' +
      escapeHtml(props.address) +
      roomBadgeHtml(props.room_type) +
      "</div>" +
      '<div class="popup-loc-grid">' +
      "<span>Memorial Union</span><strong>" +
      dmu.toFixed(2) +
      " km</strong>" +
      "<span>Silo</span><strong>" +
      ds.toFixed(2) +
      " km</strong>" +
      "<span>Conv. stores (≤800 m)</span><strong>" +
      conv +
      "</strong>" +
      "<span>Nearest conv.</span><strong>" +
      cnearTxt +
      "</strong>" +
      "</div>" +
      '<div class="popup-grid">' +
      "<span>Rent</span><strong>" +
      formatMoney(props.rent) +
      "</strong>" +
      "<span>Beds / baths</span><strong>" +
      beds +
      " / " +
      baths +
      "</strong>" +
      "<span>Sq ft</span><strong>" +
      Number(props.sqft).toLocaleString("en-US") +
      "</strong>" +
      "<span>Type</span><strong>" +
      escapeHtml(props.property_type || "") +
      "</strong>" +
      "</div>" +
      '<div class="popup-score">Opportunity score: ' +
      Number(props.opportunity_score).toFixed(1) +
      "</div>" +
      '<div class="popup-note">' +
      escapeHtml(props.score_explanation || "") +
      "</div>" +
      '<div class="popup-stops">' +
      formatStops(props) +
      "</div>" +
      '<div class="popup-actions">' +
      '<a href="' +
      portal +
      '" target="_blank" rel="noopener noreferrer">Open listings search</a>' +
      '<a href="https://unitrans.ucdavis.edu/routes" target="_blank" rel="noopener noreferrer">Unitrans routes</a>' +
      "</div>" +
      '<div class="popup-chart-box">' +
      '<div class="chart-caption">Yolo County median gross rent (U.S. Census ACS B25064) vs this listing rent scaled to that county series. Not the unit lease history.</div>' +
      '<canvas class="js-rent-chart" id="' +
      escapeHtml(cid) +
      '" width="280" height="140"></canvas>' +
      "</div>"
    );
  }

  function populatePropertyTypes(features) {
    const sel = document.getElementById("filter-type");
    const current = sel.value;
    const types = new Set();
    features.forEach(function (f) {
      const t = f.properties.property_type;
      if (t) types.add(String(t));
    });
    const sorted = Array.from(types).sort(function (a, b) {
      return a.localeCompare(b);
    });
    sel.innerHTML = '<option value="">Any</option>';
    sorted.forEach(function (t) {
      const opt = document.createElement("option");
      opt.value = t;
      opt.textContent = t;
      sel.appendChild(opt);
    });
    if (sorted.includes(current)) sel.value = current;
  }

  function redraw() {
    const filters = readFilters();
    const visible = allFeatures.filter(function (f) {
      return passesFilters(f.properties, filters);
    });

    layerGroup.clearLayers();
    markersById.clear();

    visible.forEach(function (f) {
      const p = f.properties;
      const coords = f.geometry.coordinates;
      const latlng = [coords[1], coords[0]];
      const id = featureId(f);
      const m = L.circleMarker(latlng, {
        radius: 10,
        weight: 2,
        color: "rgba(255,255,255,0.42)",
        fillColor: scoreColor(p.opportunity_score),
        fillOpacity: 0.94,
        rsProps: p,
      });
      m.bindPopup(popupHtml(f), { maxWidth: 340 });
      m.addTo(layerGroup);
      markersById.set(id, m);
    });

    updateSummary(visible);
    renderRanking(visible);
  }

  function syncMapBasemap() {
    if (!map) return;
    if (baseLayer) {
      map.removeLayer(baseLayer);
      baseLayer = null;
    }
    const isLight =
      document.documentElement.getAttribute("data-theme") === "light";
    baseLayer = isLight
      ? L.tileLayer(
          "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
          {
            attribution:
              '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ' +
              '&copy; <a href="https://carto.com/attributions">CARTO</a>',
            subdomains: "abcd",
            maxZoom: 20,
          }
        )
      : L.tileLayer(
          "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
          {
            attribution:
              '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ' +
              '&copy; <a href="https://carto.com/attributions">CARTO</a>',
            subdomains: "abcd",
            maxZoom: 20,
          }
        );
    baseLayer.addTo(map);
  }

  function setDocumentTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try {
      localStorage.setItem("rentscope-theme", t);
    } catch (err) {}
    syncMapBasemap();
  }

  function initMap() {
    map = L.map("map", { scrollWheelZoom: true, zoomControl: true }).setView(
      DAVIS_CENTER,
      13
    );

    layerGroup = L.layerGroup().addTo(map);

    map.on("popupopen", function (e) {
      const src = e.popup._source;
      const p = src && src.options ? src.options.rsProps : null;
      const el = e.popup.getElement();
      if (!el) return;
      const canvas = el.querySelector("canvas.js-rent-chart");
      openChartFromPopup(canvas, p);
    });

    map.on("popupclose", function () {
      destroyActiveChart();
    });
  }

  function initTheme() {
    let saved = null;
    try {
      saved = localStorage.getItem("rentscope-theme");
    } catch (err) {}
    const prefers =
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: light)").matches;
    document.documentElement.setAttribute(
      "data-theme",
      saved || (prefers ? "light" : "dark")
    );
    try {
      localStorage.setItem(
        "rentscope-theme",
        document.documentElement.getAttribute("data-theme")
      );
    } catch (err) {}
    syncMapBasemap();
    const btn = document.getElementById("theme-toggle");
    btn.addEventListener("click", function () {
      const cur = document.documentElement.getAttribute("data-theme");
      setDocumentTheme(cur === "light" ? "dark" : "light");
      destroyActiveChart();
    });
  }

  async function loadData() {
    const [resRent, resMkt] = await Promise.all([
      fetch(GEOJSON_URL),
      fetch(MARKET_URL),
    ]);
    if (!resRent.ok) throw new Error("Could not load rentals.geojson");
    const data = await resRent.json();
    allFeatures = data.features || [];
    if (resMkt.ok) {
      try {
        marketTrend = await resMkt.json();
      } catch (e) {
        marketTrend = [];
      }
    } else {
      marketTrend = [];
    }
    populatePropertyTypes(allFeatures);
    redraw();

    const bounds = L.latLngBounds();
    allFeatures.forEach(function (f) {
      const c = f.geometry.coordinates;
      bounds.extend([c[1], c[0]]);
    });
    if (bounds.isValid()) {
      map.fitBounds(bounds.pad(0.12));
    }

    document.body.classList.add("app-ready");
  }

  function wireFilters() {
    [
      "filter-room",
      "filter-rent",
      "filter-beds",
      "filter-score",
      "filter-type",
    ].forEach(function (id) {
      const el = document.getElementById(id);
      el.addEventListener("input", redraw);
      el.addEventListener("change", redraw);
    });
  }

  initMap();
  initTheme();
  wireFilters();
  loadData().catch(function (err) {
    console.error(err);
    document.getElementById("metric-count").textContent = "!";
    document.getElementById("metric-avg-rent").textContent = "Data error";
    document.body.classList.add("app-ready");
  });
})();
