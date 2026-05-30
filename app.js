"use strict";

const FEED_URL = "./data/feed.json";
// Show a "may be out of date" warning if the newest post is older than this.
const STALE_AFTER_DAYS = 2;
const DAY_MS = 24 * 60 * 60 * 1000;

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function renderItem(item) {
  const card = el("a", "card");
  card.href = item.url || "#";
  card.target = "_blank";
  card.rel = "noopener noreferrer";

  const meta = el("div", "card-date");
  if (item.author) meta.appendChild(el("span", "card-author", "@" + item.author));
  meta.appendChild(el("span", "card-time", fmtDate(item.date)));
  card.appendChild(meta);

  card.appendChild(el("p", "card-text", item.text || ""));

  if (item.media) {
    const img = document.createElement("img");
    img.className = "card-media";
    img.loading = "lazy";
    img.alt = "media attachment";
    img.src = item.media;
    img.addEventListener("error", () => img.remove());
    card.appendChild(img);
  }

  card.appendChild(el("div", "card-link", "View on X ↗"));
  return card;
}

function renderHeader(feed) {
  const accountEl = document.getElementById("account");
  const accountsEl = document.getElementById("accounts");
  const updatedEl = document.getElementById("updated");

  const accounts =
    Array.isArray(feed.accounts) && feed.accounts.length
      ? feed.accounts
      : feed.account
      ? [feed.account]
      : [];

  if (accounts.length === 1) {
    accountEl.textContent = "@" + accounts[0];
    document.title = "@" + accounts[0] + " · X Daily Feed";
  } else if (accounts.length > 1) {
    accountEl.textContent = "X Daily Feed";
    accountsEl.textContent = accounts.map((a) => "@" + a).join(" · ");
    accountsEl.hidden = false;
    document.title = accounts.length + " accounts · X Daily Feed";
  }

  updatedEl.textContent = feed.updated_at
    ? "Last updated " + fmtDate(feed.updated_at)
    : "";
}

function renderStaleness(items) {
  const stale = document.getElementById("stale");
  if (!items.length) return;

  let newest = 0;
  for (const it of items) {
    const t = new Date(it.date).getTime();
    if (!Number.isNaN(t) && t > newest) newest = t;
  }
  if (!newest) return;

  const ageDays = (Date.now() - newest) / DAY_MS;
  if (ageDays > STALE_AFTER_DAYS) {
    const days = Math.floor(ageDays);
    stale.textContent =
      "⚠️ This data may be out of date — the newest post is " +
      days +
      " day" +
      (days === 1 ? "" : "s") +
      " old. The scraper may be failing.";
    stale.hidden = false;
  }
}

function renderFooter(feed) {
  const sourceEl = document.getElementById("source");
  const errorEl = document.getElementById("error");

  const bits = [];
  if (feed.source) bits.push("source: " + feed.source);
  if (feed.last_success_at)
    bits.push("last successful fetch: " + fmtDate(feed.last_success_at));
  sourceEl.textContent = bits.join(" · ");

  if (feed.error_message) {
    errorEl.textContent = "Some accounts failed last run: " + feed.error_message;
    errorEl.hidden = false;
  }
}

async function load() {
  const timeline = document.getElementById("timeline");
  const status = document.getElementById("status");

  try {
    const res = await fetch(FEED_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const feed = await res.json();

    renderHeader(feed);

    const items = Array.isArray(feed.items) ? feed.items : [];
    renderStaleness(items);
    renderFooter(feed);

    timeline.innerHTML = "";
    if (items.length === 0) {
      timeline.appendChild(
        el("p", "status", "No posts yet — check back after the next update.")
      );
      return;
    }

    for (const item of items) {
      timeline.appendChild(renderItem(item));
    }
  } catch (err) {
    status.textContent = "Couldn't load the feed: " + err.message;
    status.classList.add("error");
  }
}

load();
