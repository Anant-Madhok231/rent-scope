(function () {
  "use strict";

  const DAVIS_CENTER = [38.5449, -121.749];
  const GEOJSON_URL = new URL("rentals.geojson", window.location.href).href;

  let map;
  let layerGroup;
  let allFeatures = [];
  let markersById = new Map();

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
    return (
      "$" +
      x.toLocaleString("en-US", { maximumFractionDigits: 0 })
    );
  }

  function featureId(f) {
    const p = f.properties || {};
    const c = f.geometry.coordinates || [];
    return `${p.address || ""}|${p.rent}|${c[0]}|${c[1]}`;
  }

  function passesFilters(props, filters) {
    const rent = Number(props.rent);
    const beds = Number(props.beds);
    const score = Number(props.opportunity_score);
    const ptype = String(props.property_type || "");

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

    const maxRentRaw = rentEl.value.trim();
    const minScoreRaw = scoreEl.value.trim();

    return {
      maxRent: maxRentRaw === "" ? null : Number(maxRentRaw),
      beds: bedsEl.value === "" ? null : Number(bedsEl.value),
      minScore: minScoreRaw === "" ? null : Number(minScoreRaw),
      propType: typeEl.value || null,
    };
  }

  function updateSummary(features) {
    const n = features.length;
    document.getElementById("metric-count").textContent = n.toString();

    if (!n) {
      document.getElementById("metric-avg-rent").textContent = "—";
      document.getElementById("metric-best").textContent = "—";
      document.getElementById("metric-cheapest").textContent = "—";
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

    document.getElementById("metric-avg-rent").textContent = formatMoney(rentSum / n);
    document.getElementById("metric-best").textContent = best.toFixed(1);
    document.getElementById("metric-cheapest").textContent = formatMoney(cheapest);
  }

  function renderRanking(features) {
    const list = document.getElementById("ranking-list");
    list.innerHTML = "";

    const sorted = features
      .slice()
      .sort(
        (a, b) =>
          Number(b.properties.opportunity_score) -
          Number(a.properties.opportunity_score)
      )
      .slice(0, 10);

    sorted.forEach((f) => {
      const p = f.properties;
      const id = featureId(f);
      const li = document.createElement("li");
      li.tabIndex = 0;
      li.dataset.fid = id;
      li.innerHTML =
        '<div class="rank-row-top">' +
        '<span class="rank-addr" title="' +
        escapeAttr(p.address) +
        '">' +
        escapeHtml(p.address) +
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

      const open = () => focusFeature(id);
      li.addEventListener("click", open);
      li.addEventListener("keydown", (e) => {
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

  function popupHtml(props) {
    const beds = props.beds;
    const baths = props.baths;
    return (
      '<div class="popup-title">' +
      escapeHtml(props.address) +
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
      "</div>"
    );
  }

  function populatePropertyTypes(features) {
    const sel = document.getElementById("filter-type");
    const current = sel.value;
    const types = new Set();
    features.forEach((f) => {
      const t = f.properties.property_type;
      if (t) types.add(String(t));
    });
    const sorted = Array.from(types).sort((a, b) => a.localeCompare(b));
    sel.innerHTML = '<option value="">Any</option>';
    sorted.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t;
      opt.textContent = t;
      sel.appendChild(opt);
    });
    if (sorted.includes(current)) sel.value = current;
  }

  function redraw() {
    const filters = readFilters();
    const visible = allFeatures.filter((f) =>
      passesFilters(f.properties, filters)
    );

    layerGroup.clearLayers();
    markersById.clear();

    visible.forEach((f) => {
      const p = f.properties;
      const coords = f.geometry.coordinates;
      const latlng = [coords[1], coords[0]];
      const id = featureId(f);
      const m = L.circleMarker(latlng, {
        radius: 9,
        weight: 2,
        color: "rgba(255,255,255,0.35)",
        fillColor: scoreColor(p.opportunity_score),
        fillOpacity: 0.92,
      });
      m.bindPopup(popupHtml(p), { maxWidth: 320 });
      m.addTo(layerGroup);
      markersById.set(id, m);
    });

    updateSummary(visible);
    renderRanking(visible);
  }

  function initMap() {
    map = L.map("map", { scrollWheelZoom: true, zoomControl: true }).setView(
      DAVIS_CENTER,
      13
    );

    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ' +
        '&copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: "abcd",
      maxZoom: 20,
    }).addTo(map);

    layerGroup = L.layerGroup().addTo(map);
  }

  async function loadData() {
    const res = await fetch(GEOJSON_URL);
    if (!res.ok) throw new Error("Could not load rentals.geojson");
    const data = await res.json();
    allFeatures = data.features || [];
    populatePropertyTypes(allFeatures);
    redraw();

    const bounds = L.latLngBounds();
    allFeatures.forEach((f) => {
      const c = f.geometry.coordinates;
      bounds.extend([c[1], c[0]]);
    });
    if (bounds.isValid()) {
      map.fitBounds(bounds.pad(0.12));
    }
  }

  function wireFilters() {
    ["filter-rent", "filter-beds", "filter-score", "filter-type"].forEach(
      (id) => {
        const el = document.getElementById(id);
        el.addEventListener("input", redraw);
        el.addEventListener("change", redraw);
      }
    );
  }

  initMap();
  wireFilters();
  loadData().catch((err) => {
    console.error(err);
    document.getElementById("metric-count").textContent = "!";
    document.getElementById("metric-avg-rent").textContent = "Data error";
  });
})();
