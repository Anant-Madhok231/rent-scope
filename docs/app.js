(function () {
  "use strict";

  const DAVIS_CENTER = [38.5449, -121.749];
  const GEOJSON_URL = new URL("rentals.geojson", window.location.href).href;
  const MARKET_URL = new URL("market_trend.json", window.location.href).href;

  let map;
  let layerGroup;
  let allFeatures = [];
  let markersById = new Map();
  let propsByFeatureId = new Map();
  let firstSummary = true;
  let marketTrend = [];
  let chartInstance = null;
  let baseLayer = null;
  let campusLayer = null;
  let selectedFid = null;

  const RAIL_EMPTY =
    '<p class="right-rail-placeholder">Every rental in the dataset appears on the map (sample CSV, RentCast, etc.). Choose a pin or a <strong>Top opportunities</strong> row for miles, nearby places, and scores. <strong>OffCampusReview</strong> is only an extra layer for student-written reviews when we can match a landlord—it never decides which listings exist.</p>';

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
    const name = String(props.listing_name || "").trim();
    const addr = String(props.address || "").trim();
    const q =
      (name ? name + " " : "") + addr + " Davis CA apartments for rent";
    return "https://www.google.com/search?q=" + encodeURIComponent(q);
  }

  /** Single-line label for tooltips / native marker title (name - address) */
  function listingTitle(props) {
    const name = String(props.listing_name || "").trim();
    const addr = String(props.address || "").trim();
    if (name && addr) return name + " - " + addr;
    if (addr) return addr;
    return name || "Listing";
  }

  /**
   * One line: "Complex name - full address" plus room badge (every rental listing, not OffCampus-only).
   */
  function listingRowHtml(props, variant) {
    const name = String(props.listing_name || "").trim();
    const addr = String(props.address || "").trim();
    let line = "";
    if (name && addr) {
      line = escapeHtml(name) + " - " + escapeHtml(addr);
    } else if (addr) {
      line = escapeHtml(addr);
    } else {
      line = escapeHtml(name || "Listing");
    }
    const vcls = variant ? " listing-row--" + variant : "";
    const inner =
      '<span class="listing-line">' +
      line +
      "</span>" +
      roomBadgeHtml(props.room_type);
    if (variant === "modal") {
      return (
        '<span class="listing-row listing-row--modal">' + inner + "</span>"
      );
    }
    return '<div class="listing-row' + vcls + '">' + inner + "</div>";
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

  function ocrMatched(props) {
    return props.offcampus_match === true || props.offcampus_match === "true";
  }

  function rentScopeDivIcon(p) {
    const fill = scoreColor(p.opportunity_score);
    const matched = ocrMatched(p);
    const av = p.offcampus_avg_rating;
    const cnt = Number(p.offcampus_review_count) || 0;
    let chip = "";
    if (matched) {
      if (av != null && Number.isFinite(Number(av))) {
        chip =
          '<div class="rs-ocr-chip" title="OffCampusReview rating"><span class="rs-ocr-num">' +
          Number(av).toFixed(1) +
          '</span><span class="rs-ocr-n">' +
          cnt +
          "</span></div>";
      } else if (cnt > 0) {
        chip =
          '<div class="rs-ocr-chip rs-ocr-chip--lite" title="OffCampusReview reviews">' +
          '<span class="rs-ocr-lite">' +
          cnt +
          " rev</span></div>";
      }
    }
    const html =
      '<div class="rs-pin"><span class="rs-pin-dot" style="background:' +
      fill +
      '"></span>' +
      chip +
      "</div>";
    return L.divIcon({
      html: html,
      className: "rs-div-icon",
      iconSize: [58, 40],
      iconAnchor: [29, 34],
      popupAnchor: [0, -30],
    });
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

  function rankOcrStrip(p, id) {
    const matched = ocrMatched(p);
    const av = p.offcampus_avg_rating;
    const cnt = Number(p.offcampus_review_count) || 0;
    if (!matched || av == null || !Number.isFinite(Number(av))) {
      return (
        '<div class="rank-ocr rank-ocr--na"><span class="rank-ocr-label">OCR</span> —</div>'
      );
    }
    return (
      '<div class="rank-ocr">' +
      '<span class="rank-ocr-label">OCR</span>' +
      '<span class="rank-ocr-score">' +
      Number(av).toFixed(1) +
      "</span>" +
      '<span class="rank-ocr-count">' +
      cnt +
      " reviews</span>" +
      '<button type="button" class="rank-ocr-btn js-ocr-open" data-fid="' +
      escapeAttr(id) +
      '">Read</button>' +
      "</div>"
    );
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
        '<div class="rank-addr" title="' +
        escapeAttr(listingTitle(p)) +
        '">' +
        listingRowHtml(p, "rank") +
        "</div>" +
        '<span class="rank-score">' +
        Number(p.opportunity_score).toFixed(1) +
        "</span></div>" +
        rankOcrStrip(p, id) +
        '<div class="rank-meta">' +
        formatMoney(p.rent) +
        " · " +
        p.beds +
        " bd · " +
        escapeHtml(p.property_type || "") +
        "</div>";

      li.addEventListener("click", function (ev) {
        if (ev.target.closest(".js-ocr-open")) return;
        focusFeature(id);
      });
      li.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          focusFeature(id);
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
    selectedFid = id;
    const m = markersById.get(id);
    if (m) {
      map.setView(m.getLatLng(), Math.max(map.getZoom(), 15), { animate: true });
      m.openPopup();
    } else {
      updateRightRail(null);
    }
  }

  function milesFromProps(miProp, kmFallback) {
    if (miProp != null && miProp !== "" && Number.isFinite(Number(miProp))) {
      return Number(miProp);
    }
    if (
      kmFallback != null &&
      kmFallback !== "" &&
      Number.isFinite(Number(kmFallback))
    ) {
      return Number(kmFallback) * 0.621371;
    }
    return null;
  }

  function fmtMiles(miProp, kmFallback) {
    const m = milesFromProps(miProp, kmFallback);
    if (m == null) return "—";
    return m.toFixed(2) + " mi";
  }

  function fmtIndex100(x) {
    if (x == null || x === "" || !Number.isFinite(Number(x))) return "—";
    return Number(x).toFixed(0) + "/100";
  }

  function offcampusActionUrl(props) {
    const matched = ocrMatched(props);
    const landlord = String(props.offcampus_landlord_url || "").trim();
    if (matched && landlord) return landlord;
    return (
      props.offcampus_school_url ||
      "https://www.offcampusreview.com/school/uc-davis"
    );
  }

  function formatNearbyPois(items, title) {
    if (!Array.isArray(items) || !items.length) {
      return (
        '<div class="rail-section-title">' +
        escapeHtml(title) +
        '</div><p class="popup-note">No named places mapped in OpenStreetMap within ~1.5 mi.</p>'
      );
    }
    let h =
      '<div class="rail-section-title">' +
      escapeHtml(title) +
      '</div><ul class="rail-poi-list">';
    items.forEach(function (x) {
      const mi = Number(x.mi);
      const dist = Number.isFinite(mi) ? mi.toFixed(2) + " mi" : "—";
      h +=
        "<li><strong>" +
        escapeHtml(x.name || "") +
        "</strong> · " +
        dist +
        '<span class="rail-poi-kind">' +
        escapeHtml(x.kind || "") +
        "</span></li>";
    });
    h += "</ul>";
    return h;
  }

  function listingDetailHtml(props) {
    const mu = fmtMiles(
      props.dist_mi_memorial_union,
      props.dist_km_memorial_union
    );
    const silo = fmtMiles(props.dist_mi_silo, props.dist_km_silo);
    const campus = fmtMiles(props.dist_mi_campus, null);
    const downtown = fmtMiles(props.dist_mi_downtown, null);
    const conv = Number(props.convenience_800m_count);
    const cnear = fmtMiles(
      props.convenience_nearest_mi,
      props.convenience_nearest_km
    );
    const grocery = props.nearby_grocery;
    const food = props.nearby_food;
    let h = '<div class="rail-section-title">Distances (straight-line miles)</div>';
    h +=
      '<div class="popup-loc-grid">' +
      "<span>UC Davis core</span><strong>" +
      campus +
      "</strong>" +
      "<span>Downtown Davis</span><strong>" +
      downtown +
      "</strong>" +
      "<span>Memorial Union</span><strong>" +
      mu +
      "</strong>" +
      "<span>Silo</span><strong>" +
      silo +
      "</strong>" +
      "<span>Conv. stores (≤800 m)</span><strong>" +
      conv +
      "</strong>" +
      "<span>Nearest convenience</span><strong>" +
      cnear +
      "</strong>" +
      "</div>";
    h +=
      '<p class="popup-note" style="margin-top:0.45rem">Straight-line (spherical) distance from OpenStreetMap reference points—not driving miles.</p>';
    h += formatNearbyPois(grocery, "Nearest groceries & convenience (named)");
    h += formatNearbyPois(food, "Nearest dining & cafés (named)");
    h += '<div class="rail-section-title">Bike / scooter & traffic (estimated)</div>';
    h +=
      '<div class="rail-metric-row"><span>Bike / e-scooter friendliness</span><strong>' +
      fmtIndex100(props.bike_escooter_index) +
      "</strong></div>";
    h +=
      '<div class="rail-metric-row"><span>Traffic exposure (higher = calmer)</span><strong>' +
      fmtIndex100(props.traffic_calm_index) +
      "</strong></div>";
    h +=
      '<p class="popup-note">Modeled from OSM cycleways, bike parking, and distance to major roads—not live traffic data.</p>';
    h += '<div class="rail-section-title">Parking (OpenStreetMap)</div>';
    h +=
      '<p class="popup-note">' +
      escapeHtml(
        props.parking_summary ||
          "No fee-tagged parking mapped very close in OSM."
      ) +
      "</p>";
    return h;
  }

  function formatRightRail(props, fid) {
    const reviewUrl = escapeAttr(offcampusActionUrl(props));
    return (
      '<div class="rail-addr">' + listingRowHtml(props, "rail") + "</div>" +
      formatOffcampusBlock(props, fid) +
      listingDetailHtml(props) +
      '<div class="right-rail-actions" style="margin-top:0.65rem">' +
      '<a class="rail-btn rail-btn-primary" href="' +
      reviewUrl +
      '" target="_blank" rel="noopener noreferrer">Leave a review / add place on OffCampusReview</a>' +
      "</div>" +
      '<p class="rail-disclaimer">POI names and parking fees come from community OpenStreetMap data and may be incomplete. Always verify on site.</p>'
    );
  }

  function updateRightRail(fid) {
    const body = document.getElementById("right-rail-body");
    if (!body) return;
    if (!fid || !propsByFeatureId.has(fid)) {
      body.innerHTML = RAIL_EMPTY;
      return;
    }
    body.innerHTML = formatRightRail(propsByFeatureId.get(fid), fid);
  }

  function closeOcrModal() {
    const modal = document.getElementById("ocr-modal");
    if (!modal) return;
    modal.classList.remove("is-open");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("modal-open");
  }

  function openOcrModal(props) {
    if (!props) return;
    const modal = document.getElementById("ocr-modal");
    const titleEl = document.getElementById("ocr-modal-title");
    const subEl = document.getElementById("ocr-modal-sub");
    const listEl = document.getElementById("ocr-modal-list");
    const footEl = document.getElementById("ocr-modal-foot");
    if (!modal || !titleEl || !subEl || !listEl || !footEl) return;

    const matched = ocrMatched(props);
    const url = String(props.offcampus_landlord_url || "").trim();
    const name = props.offcampus_landlord_name || "OffCampusReview";
    const school = props.offcampus_school_url || "https://www.offcampusreview.com/school/uc-davis";
    const brand = props.offcampus_brand_url || "https://www.offcampusreview.com/";

    titleEl.innerHTML = listingRowHtml(props, "modal");
    if (!matched || !url) {
      subEl.innerHTML =
        '<p class="ocr-modal-lead">No OffCampusReview profile is linked to this listing yet, so there are no student reviews to show here.</p>' +
        '<p class="ocr-modal-lead">If you lived here, open the <a href="' +
        escapeAttr(school) +
        '" target="_blank" rel="noopener noreferrer">UC Davis hub on OffCampusReview</a> to add the place or leave the first review.</p>';
      listEl.innerHTML = "";
      footEl.innerHTML =
        '<a class="ocr-modal-cta" href="' +
        escapeAttr(school) +
        '" target="_blank" rel="noopener noreferrer">OffCampusReview · UC Davis</a>';
    } else {
      const av = props.offcampus_avg_rating;
      const cnt = Number(props.offcampus_review_count) || 0;
      let lead =
        '<div class="ocr-modal-hero"><div class="ocr-modal-big">' +
        (av != null && Number.isFinite(Number(av)) ? Number(av).toFixed(1) : "—") +
        '</div><div class="ocr-modal-hero-meta"><span class="ocr-modal-stars">student avg</span><span class="ocr-modal-rcount">' +
        cnt +
        " reviews on OffCampusReview</span></div></div>";
      lead +=
        '<p class="ocr-modal-landlord"><a href="' +
        escapeAttr(url) +
        '" target="_blank" rel="noopener noreferrer">' +
        escapeHtml(name) +
        "</a> · open full profile for more</p>";
      subEl.innerHTML = lead;

      const revs = props.offcampus_reviews;
      let listHtml = "";
      if (cnt === 0) {
        listHtml =
          "<p class=\"ocr-modal-empty\">No OffCampus reviews yet. If you lived here, be the first to leave one on OffCampusReview.</p>";
      } else if (Array.isArray(revs) && revs.length) {
        revs.forEach(function (rv) {
          listHtml +=
            '<article class="ocr-modal-card"><div class="ocr-modal-card-head"><span class="ocr-modal-card-r">' +
            (rv.rating != null ? String(rv.rating) : "—") +
            '/5</span><span class="ocr-modal-card-d">' +
            escapeHtml(rv.date || "") +
            "</span></div>";
          if (rv.propertyAddress) {
            listHtml +=
              '<div class="ocr-modal-card-addr">' +
              escapeHtml(rv.propertyAddress) +
              "</div>";
          }
          listHtml +=
            '<p class="ocr-modal-card-text">' + escapeHtml(rv.text || "") + "</p></article>";
        });
      } else {
        listHtml =
          "<p class=\"ocr-modal-empty\">No review excerpts in this export yet. Open OffCampusReview for full text—or add a review if you lived here.</p>";
      }
      listEl.innerHTML = listHtml;
      footEl.innerHTML =
        '<a class="ocr-modal-cta" href="' +
        escapeAttr(url) +
        '" target="_blank" rel="noopener noreferrer">All reviews on OffCampusReview</a>';
    }

    modal.classList.add("is-open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("modal-open");
  }

  function wireOcrModal() {
    document.body.addEventListener("click", function (ev) {
      const t = ev.target.closest(".js-ocr-open");
      if (!t) return;
      ev.preventDefault();
      const fid = t.getAttribute("data-fid");
      if (!fid || !propsByFeatureId.has(fid)) return;
      destroyActiveChart();
      if (map) map.closePopup();
      openOcrModal(propsByFeatureId.get(fid));
    });
    document.body.addEventListener("click", function (ev) {
      if (ev.target.closest("[data-close-modal]")) closeOcrModal();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeOcrModal();
    });
  }

  function formatOffcampusBlock(props, fid) {
    const brand = escapeAttr(
      props.offcampus_brand_url || "https://www.offcampusreview.com/"
    );
    const school = escapeAttr(
      props.offcampus_school_url ||
        "https://www.offcampusreview.com/school/uc-davis"
    );
    const matched = ocrMatched(props);
    const url = String(props.offcampus_landlord_url || "").trim();
    const name = escapeHtml(props.offcampus_landlord_name || "");
    const avg = props.offcampus_avg_rating;
    const cnt = Number(props.offcampus_review_count) || 0;
    const fidAttr = escapeAttr(fid);

    let html = '<div class="ocr-wrap ocr-wrap--hero">';
    html +=
      '<div class="ocr-hero-top"><a href="' +
      brand +
      '" target="_blank" rel="noopener noreferrer" class="ocr-brand-lg">OffCampusReview</a>';
    html +=
      '<span class="ocr-hero-tag">Optional · student reviews (not the listing source)</span></div>';

    if (!matched || !url) {
      html +=
        '<p class="ocr-note-lg">No OffCampusReview entry matched this rental yet, so there are no student reviews to show. If you lived here, you can <a href="' +
        school +
        '" target="_blank" rel="noopener noreferrer">add the place or leave the first review</a> on OffCampusReview (UC Davis).</p></div>';
      return html;
    }

    html += '<div class="ocr-hero-scoreline">';
    if (avg != null && Number.isFinite(Number(avg))) {
      html +=
        '<span class="ocr-big-num">' +
        Number(avg).toFixed(1) +
        '</span><span class="ocr-big-denom">/5</span>';
      html +=
        '<span class="ocr-big-count">' +
        cnt +
        " reviews</span>";
    } else {
      html +=
        '<span class="ocr-big-count">' +
        cnt +
        " reviews on OffCampusReview</span>";
    }
    html += "</div>";

    if (cnt === 0) {
      html +=
        '<p class="ocr-note-lg" style="margin-top:0.55rem">No reviews posted yet—if you lived here, leave one on OffCampusReview to help the next renter.</p>';
    }

    html +=
      '<p class="ocr-landlord-lg"><a href="' +
      escapeAttr(url) +
      '" target="_blank" rel="noopener noreferrer">' +
      name +
      "</a></p>";

    html +=
      '<div class="ocr-hero-actions">' +
      '<button type="button" class="ocr-btn-primary js-ocr-open" data-fid="' +
      fidAttr +
      '">Read reviews here</button>' +
      '<a class="ocr-btn-secondary" href="' +
      escapeAttr(url) +
      '" target="_blank" rel="noopener noreferrer">Open OffCampusReview</a>' +
      "</div>";

    html +=
      '<p class="ocr-disclaim-sm">Excerpts from public OffCampusReview data · not authored by RentScope</p></div>';
    return html;
  }

  function formatStops(props) {
    const stops = props.unitrans_stops;
    if (!Array.isArray(stops) || !stops.length) {
      return "<p class=\"popup-stops-title\">No Unitrans stops in OSM within ~0.9 mi.</p>";
    }
    let html = '<div class="popup-stops-title">Unitrans nearby (~0.9 mi)</div><ul>';
    stops.forEach(function (s) {
      const name = escapeHtml(s.name || "Stop");
      let mi = Number(s.mi);
      if (!Number.isFinite(mi) && s.km != null) {
        mi = Number(s.km) * 0.621371;
      }
      const dist = Number.isFinite(mi) ? mi.toFixed(2) + " mi" : "—";
      html += "<li><strong>" + name + "</strong> · " + dist + "</li>";
    });
    html += "</ul>";
    return html;
  }

  function popupHtml(f) {
    const props = f.properties;
    const fid = featureId(f);
    const cid = chartDomId(f);
    const beds = props.beds;
    const baths = props.baths;
    const portal = escapeAttr(listingHref(props));
    const reviewUrl = escapeAttr(offcampusActionUrl(props));
    return (
      '<div class="popup-title-wrap">' + listingRowHtml(props, "popup") + "</div>" +
      formatOffcampusBlock(props, fid) +
      listingDetailHtml(props) +
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
      '<a href="' +
      reviewUrl +
      '" target="_blank" rel="noopener noreferrer">OffCampusReview · review / add</a>' +
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
    const prevSel = selectedFid;

    layerGroup.clearLayers();
    markersById.clear();
    propsByFeatureId.clear();

    visible.forEach(function (f) {
      const p = f.properties;
      const coords = f.geometry.coordinates;
      const latlng = [coords[1], coords[0]];
      const id = featureId(f);
      propsByFeatureId.set(id, p);
      const m = L.marker(latlng, {
        icon: rentScopeDivIcon(p),
        title: listingTitle(p),
        rsProps: p,
        rsFid: id,
      });
      m.bindPopup(popupHtml(f), {
        maxWidth: 420,
        className: "rs-popup-wrap",
        autoPanPadding: [24, 24],
      });
      m.addTo(layerGroup);
      markersById.set(id, m);
    });

    if (prevSel && propsByFeatureId.has(prevSel)) {
      selectedFid = prevSel;
      updateRightRail(prevSel);
    } else {
      selectedFid = null;
      updateRightRail(null);
    }

    updateSummary(visible);
    renderRanking(visible);
  }

  function syncMapBasemap() {
    if (!map) return;
    if (baseLayer) {
      map.removeLayer(baseLayer);
      baseLayer = null;
    }
    if (campusLayer) {
      map.removeLayer(campusLayer);
      campusLayer = null;
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

    if (typeof L !== "undefined" && L.esri && L.esri.dynamicMapLayer) {
      try {
        if (!map.getPane("ucdCampusPane")) {
          map.createPane("ucdCampusPane");
          map.getPane("ucdCampusPane").style.zIndex = 350;
        }
        campusLayer = L.esri.dynamicMapLayer({
          url:
            "https://gis.ucdavis.edu/server/rest/services/Base_UC_Davis_Basemap/MapServer",
          pane: "ucdCampusPane",
          opacity: isLight ? 0.74 : 0.66,
          attribution:
            'Campus map © <a href="https://campusmap.ucdavis.edu/">UC Davis</a>',
        });
        campusLayer.addTo(map);
      } catch (err) {
        console.warn("UC Davis campus layer skipped:", err);
        campusLayer = null;
      }
    }
  }

  function setDocumentTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try {
      localStorage.setItem("rentscope-theme", t);
    } catch (err) {}
    syncMapBasemap();
  }

  function initMap() {
    map = L.map("map", {
      scrollWheelZoom: true,
      zoomControl: true,
      zoomSnap: 0.25,
      zoomDelta: 0.5,
      wheelPxPerZoomLevel: 88,
      wheelDebounceTime: 36,
      tap: true,
      tapTolerance: 18,
      inertia: true,
      inertiaDeceleration: 2600,
      inertiaMaxSpeed: 2400,
      worldCopyJump: true,
      bounceAtZoomLimits: false,
      preferCanvas: false,
    }).setView(DAVIS_CENTER, 13);

    layerGroup = L.layerGroup().addTo(map);

    map.on("popupopen", function (e) {
      const src = e.popup._source;
      const p = src && src.options ? src.options.rsProps : null;
      const fid = src && src.options ? src.options.rsFid : null;
      if (fid) {
        selectedFid = fid;
        updateRightRail(fid);
      }
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
    try {
      syncMapBasemap();
    } catch (err) {
      console.error("syncMapBasemap failed:", err);
    }
    const btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.addEventListener("click", function () {
        const cur = document.documentElement.getAttribute("data-theme");
        setDocumentTheme(cur === "light" ? "dark" : "light");
        destroyActiveChart();
      });
    }
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
      if (!el) return;
      el.addEventListener("input", redraw);
      el.addEventListener("change", redraw);
    });
  }

  wireOcrModal();
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
