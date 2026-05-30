"use strict";

const FEED_URL = "./data/feed.json";

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

  card.appendChild(el("div", "card-date", fmtDate(item.date)));
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

async function load() {
  const timeline = document.getElementById("timeline");
  const status = document.getElementById("status");
  const accountEl = document.getElementById("account");
  const updatedEl = document.getElementById("updated");

  try {
    const res = await fetch(FEED_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const feed = await res.json();

    if (feed.account) {
      accountEl.textContent = "@" + feed.account;
      document.title = "@" + feed.account + " · X Daily Feed";
    }
    updatedEl.textContent = feed.updated_at
      ? "Last updated " + fmtDate(feed.updated_at)
      : "";

    const items = Array.isArray(feed.items) ? feed.items : [];
    timeline.innerHTML = "";

    if (items.length === 0) {
      const empty = el("p", "status", "No posts yet — check back after the next update.");
      timeline.appendChild(empty);
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
