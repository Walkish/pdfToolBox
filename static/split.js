"use strict";

/* The Split tab: one document, held open on the server while its pages are
   dropped, reordered and turned.

   Unlike the other three tabs, the file is uploaded once and then stays put.
   What travels afterwards is a page order, not a file -- which is why this
   tab has its own state and its own run, rather than joining TABS in app.js.

   SPLIT.pages holds the pages still wanted, in the order they will be
   written. Each entry keeps its own `number`: the page's number in the
   uploaded document, one-based, which is what the server addresses and what
   the user sees on the tile. Position in the array is the new order; the
   number never changes with it. */
var SPLIT = { jobId: null, name: null, pageCount: 0, pages: [] };

function splitPageUrl(number, extension, query) {
  return "/api/split/" + SPLIT.jobId + "/pages/" + number + extension + (query || "");
}

function allPages(count) {
  var pages = [];
  var number;
  for (number = 1; number <= count; number += 1) {
    pages.push({ number: number, rotation: 0 });
  }
  return pages;
}

function splitStatus(message) {
  var note = byId("split-status");
  note.textContent = message || "";
  note.hidden = !message;
}

/* A session that is gone takes the document with it: every page URL now
   404s, so the grid is cleared rather than left showing broken tiles. */
function splitExpired() {
  SPLIT.jobId = null;
  SPLIT.pages = [];
  renderSplitPages();
  splitStatus("That document is no longer open — load it again.");
}

function openDocument(fileList) {
  if (!fileList.length) { return; }
  var form = new FormData();
  form.append("files", fileList[0]);
  splitStatus("Reading the document…");
  byId("split-results").innerHTML = "";
  byId("split-zip").hidden = true;

  fetch("/api/split", { method: "POST", body: form }).then(function (response) {
    return response.json().catch(function () { return {}; }).then(function (data) {
      if (!response.ok) {
        throw new Error(data.description || ("Could not open the document (" + response.status + ")"));
      }
      return data;
    });
  }).then(function (payload) {
    SPLIT.jobId = payload.job_id;
    SPLIT.name = payload.name;
    SPLIT.pageCount = payload.page_count;
    SPLIT.pages = allPages(payload.page_count);
    splitStatus(payload.name + " — " + payload.page_count + (payload.page_count === 1 ? " page" : " pages"));
    renderSplitPages();
  }).catch(function (error) {
    SPLIT.jobId = null;
    SPLIT.pages = [];
    renderSplitPages();
    splitStatus(error.message);
  });
}

function renderSplitPages() {
  var grid = byId("split-pages");
  grid.innerHTML = "";
  SPLIT.pages.forEach(function (page, index) {
    grid.appendChild(pageTile(page, index));
  });
  byId("split-run").disabled = SPLIT.pages.length === 0;
  byId("split-reset").hidden = SPLIT.jobId === null ||
    (SPLIT.pages.length === SPLIT.pageCount && isOriginalOrder());
}

function isOriginalOrder() {
  return SPLIT.pages.every(function (page, index) {
    return page.number === index + 1 && page.rotation === 0;
  });
}

function pageTile(page, index) {
  var item = document.createElement("li");
  item.className = "page";

  var frame = document.createElement("div");
  frame.className = "pthumb";
  var thumb = document.createElement("img");
  /* Lazily: a two-hundred-page document renders only the pages that are
     actually scrolled to. The URL names one page of one job and its content
     cannot change, so the browser is free to cache it across the re-renders
     every arrow press causes. */
  thumb.loading = "lazy";
  thumb.src = splitPageUrl(page.number, ".png");
  thumb.alt = "Page " + page.number;
  thumb.style.transform = "rotate(" + page.rotation + "deg)";
  thumb.addEventListener("error", function () { thumb.classList.add("is-empty"); });
  frame.appendChild(thumb);
  item.appendChild(frame);

  var label = document.createElement("span");
  label.className = "pnum";
  label.textContent = "Page " + page.number;
  item.appendChild(label);

  var buttons = document.createElement("div");
  buttons.className = "pbuttons";
  buttons.appendChild(movePageButton(index, -1, "←", "Move earlier", index === 0));
  buttons.appendChild(
    movePageButton(index, 1, "→", "Move later", index === SPLIT.pages.length - 1)
  );
  buttons.appendChild(turnPageButton(page, -90, "↺", "Turn left"));
  buttons.appendChild(turnPageButton(page, 90, "↻", "Turn right"));
  buttons.appendChild(pageDownloadLink(page));
  buttons.appendChild(dropPageButton(index));
  item.appendChild(buttons);
  return item;
}

function movePageButton(index, delta, glyph, title, disabled) {
  var button = document.createElement("button");
  button.className = "icon";
  button.textContent = glyph;
  button.title = title;
  button.disabled = disabled;
  button.addEventListener("click", function () {
    var moved = SPLIT.pages.splice(index, 1)[0];
    SPLIT.pages.splice(index + delta, 0, moved);
    renderSplitPages();
  });
  return button;
}

function turnPageButton(page, delta, glyph, title) {
  var button = document.createElement("button");
  button.className = "icon";
  button.textContent = glyph;
  button.title = title;
  button.addEventListener("click", function () {
    // Kept in [0, 360) so the value sent is always one of 0/90/180/270, which
    // is all the server accepts.
    page.rotation = (((page.rotation + delta) % 360) + 360) % 360;
    renderSplitPages();
  });
  return button;
}

/* A plain link, not a fetch: the page is built on demand from the document
   the server already holds, so there is nothing to upload and nothing to
   wait for before the button works. */
function pageDownloadLink(page) {
  var link = document.createElement("a");
  link.className = "icon";
  link.textContent = "⤓";
  link.title = "Download this page";
  link.href = splitPageUrl(page.number, ".pdf", "?rotate=" + page.rotation);
  return link;
}

function dropPageButton(index) {
  var button = document.createElement("button");
  button.className = "icon";
  button.textContent = "✕";
  button.title = "Drop this page";
  button.addEventListener("click", function () {
    SPLIT.pages.splice(index, 1);
    renderSplitPages();
  });
  return button;
}

function runSplit() {
  var button = byId("split-run");
  var results = byId("split-results");
  var zipLink = byId("split-zip");
  var originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = "Working…";
  results.innerHTML = "";
  zipLink.hidden = true;

  var form = new FormData();
  SPLIT.pages.forEach(function (page) {
    form.append("order", String(page.number));
    // One entry per page, always, including zeros: that is what lines the
    // rotations up with the order index for index on the server.
    form.append("rotations", String(page.rotation));
  });
  if (byId("split-normalize").checked) {
    form.append("normalize", "1");
  }

  fetch("/api/split/" + SPLIT.jobId + "/build", { method: "POST", body: form }).then(function (response) {
    if (response.status === 404) { throw new Error("expired"); }
    return response.json().catch(function () { return {}; }).then(function (data) {
      if (!response.ok) {
        throw new Error(data.description || ("Request failed: " + response.status));
      }
      return data;
    });
  }).then(function (payload) {
    payload.results.forEach(function (result) {
      results.appendChild(resultRow(result));
    });
    if (payload.zip_url && SPLIT.pages.length > 1) {
      zipLink.href = payload.zip_url;
      zipLink.hidden = false;
    }
  }).catch(function (error) {
    if (error.message === "expired") {
      splitExpired();
      return;
    }
    var item = document.createElement("li");
    item.className = "result failed";
    item.textContent = error.message;
    results.appendChild(item);
  }).then(function () {
    button.textContent = originalLabel;
    button.disabled = SPLIT.pages.length === 0;
  });
}

(function initSplit() {
  wireDropZone("split", openDocument);
  byId("split-run").addEventListener("click", runSplit);
  byId("split-reset").addEventListener("click", function () {
    SPLIT.pages = allPages(SPLIT.pageCount);
    renderSplitPages();
  });
}());
