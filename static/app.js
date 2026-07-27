"use strict";

/* Three tabs, each owning an ordered file list and a results list.
   State is one object per tab; no framework, no build step. */

var TABS = {
  compress: { files: [], endpoint: "/api/compress", multiResult: true },
  merge: { files: [], endpoint: "/api/merge", multiResult: false },
  images: { files: [], endpoint: "/api/images", multiResult: false }
};

function byId(id) { return document.getElementById(id); }

function humanSize(bytes) {
  if (bytes < 1024) { return bytes + " B"; }
  if (bytes < 1048576) { return (bytes / 1024).toFixed(1) + " KB"; }
  return (bytes / 1048576).toFixed(1) + " MB";
}

function activateTab(name) {
  var tabs = document.querySelectorAll(".tab");
  var panels = document.querySelectorAll(".panel");
  var i;
  for (i = 0; i < tabs.length; i += 1) {
    tabs[i].classList.toggle("is-active", tabs[i].dataset.tab === name);
  }
  for (i = 0; i < panels.length; i += 1) {
    panels[i].classList.toggle("is-active", panels[i].id === "panel-" + name);
  }
}

function renderFileList(tabName) {
  var state = TABS[tabName];
  var list = byId(tabName + "-list");
  list.innerHTML = "";
  state.files.forEach(function (file, index) {
    var item = document.createElement("li");
    var name = document.createElement("span");
    name.className = "fname";
    name.textContent = file.name;
    var size = document.createElement("span");
    size.className = "fsize";
    size.textContent = humanSize(file.size);
    item.appendChild(name);
    item.appendChild(size);

    if (tabName !== "compress") {
      item.appendChild(orderButton(tabName, index, -1, "↑", index === 0));
      item.appendChild(
        orderButton(tabName, index, 1, "↓", index === state.files.length - 1)
      );
    }
    var remove = document.createElement("button");
    remove.className = "icon";
    remove.textContent = "✕";
    remove.title = "Remove";
    remove.addEventListener("click", function () {
      state.files.splice(index, 1);
      renderFileList(tabName);
    });
    item.appendChild(remove);
    list.appendChild(item);
  });
  byId(tabName + "-run").disabled = state.files.length === 0;
}

function orderButton(tabName, index, delta, glyph, disabled) {
  var button = document.createElement("button");
  button.className = "icon";
  button.textContent = glyph;
  button.disabled = disabled;
  button.addEventListener("click", function () {
    var files = TABS[tabName].files;
    var target = index + delta;
    var moved = files.splice(index, 1)[0];
    files.splice(target, 0, moved);
    renderFileList(tabName);
  });
  return button;
}

function addFiles(tabName, fileList) {
  var i;
  for (i = 0; i < fileList.length; i += 1) {
    TABS[tabName].files.push(fileList[i]);
  }
  renderFileList(tabName);
}

function selectedPreset(tabName) {
  var chosen = document.querySelector("input[name='" + tabName + "-preset']:checked");
  return chosen ? chosen.value : "print300";
}

function resultRow(result) {
  var item = document.createElement("li");
  item.className = result.ok ? "result ok" : "result failed";

  var head = document.createElement("div");
  head.className = "rhead";
  var name = document.createElement("strong");
  name.textContent = result.name;
  head.appendChild(name);

  if (result.ok) {
    var sizes = document.createElement("span");
    sizes.className = "sizes";
    if (result.size_after !== result.size_before) {
      sizes.textContent = humanSize(result.size_before) + " → " +
        humanSize(result.size_after) + "  (−" + result.saved_pct + "%)";
    } else {
      sizes.textContent = humanSize(result.size_after);
    }
    head.appendChild(sizes);

    if (result.below_print_floor === true) {
      // The print guarantee is the point of this tool, so it gets its own
      // badge instead of being one paragraph among unrelated notices.
      var badge = document.createElement("span");
      badge.className = "badge floor";
      badge.textContent = "Below print floor";
      head.appendChild(badge);
    }

    var link = document.createElement("a");
    link.href = result.download_url;
    link.textContent = "Download";
    link.className = "download";
    head.appendChild(link);
  }
  item.appendChild(head);

  (result.warnings || []).forEach(function (warning) {
    var note = document.createElement("p");
    note.className = "warning";
    note.textContent = warning;
    item.appendChild(note);
  });

  if (!result.ok) {
    var error = document.createElement("p");
    error.className = "error";
    error.textContent = result.error;
    item.appendChild(error);
    if (result.stderr) {
      var details = document.createElement("details");
      var summary = document.createElement("summary");
      summary.textContent = "Tool output";
      var pre = document.createElement("pre");
      pre.textContent = result.stderr;
      details.appendChild(summary);
      details.appendChild(pre);
      item.appendChild(details);
    }
  }

  if (result.ok && result.preview_url) {
    item.appendChild(previewBlock(result.preview_url));
  }
  return item;
}

function previewBlock(previewUrl) {
  var details = document.createElement("details");
  details.className = "preview";
  var summary = document.createElement("summary");
  summary.textContent = "Check readability (before / after)";
  details.appendChild(summary);
  var body = document.createElement("div");
  body.className = "pbody";
  body.textContent = "Rendering…";
  details.appendChild(body);

  var loaded = false;
  details.addEventListener("toggle", function () {
    if (!details.open || loaded) { return; }
    loaded = true;
    fetch(previewUrl).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        return { ok: response.ok, data: data };
      });
    }).then(function (result) {
      var data = result.data;
      body.textContent = "";
      if (!result.ok) {
        // Error bodies are {error: <exception name>, description: <message>};
        // `description` is the part worth reading.
        body.textContent = data.description || data.error ||
          "Could not render the comparison.";
        return;
      }
      var caption = document.createElement("p");
      caption.className = "hint";
      caption.textContent = "Page " + data.page + ", rendered at " + data.dpi +
        " dpi and shown at 1:1.";
      body.appendChild(caption);
      var pair = document.createElement("div");
      pair.className = "pair";
      ["before", "after"].forEach(function (side) {
        var figure = document.createElement("figure");
        var image = document.createElement("img");
        image.src = data[side];
        image.alt = side + " compression";
        var caption2 = document.createElement("figcaption");
        caption2.textContent = side === "before" ? "Original" : "Compressed";
        figure.appendChild(image);
        figure.appendChild(caption2);
        pair.appendChild(figure);
      });
      body.appendChild(pair);
    }).catch(function () {
      body.textContent = "Could not render the comparison.";
    });
  });
  return details;
}

function run(tabName) {
  var state = TABS[tabName];
  var button = byId(tabName + "-run");
  var results = byId(tabName + "-results");
  var zipLink = byId(tabName + "-zip");
  button.disabled = true;
  var originalLabel = button.textContent;
  button.textContent = "Working…";
  results.innerHTML = "";
  if (zipLink) { zipLink.hidden = true; }

  var form = new FormData();
  state.files.forEach(function (file) { form.append("files", file); });
  if (tabName === "compress") {
    form.append("preset", selectedPreset("compress"));
  }
  if (tabName === "merge" && byId("merge-compress").checked) {
    form.append("compress", "1");
    form.append("preset", selectedPreset("merge"));
  }

  fetch(state.endpoint, { method: "POST", body: form }).then(function (response) {
    if (!response.ok) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        throw new Error(data.description || ("Request failed: " + response.status));
      });
    }
    return response.json();
  }).then(function (payload) {
    payload.results.forEach(function (result) {
      results.appendChild(resultRow(result));
    });
    if (zipLink && payload.zip_url && payload.results.length > 1) {
      zipLink.href = payload.zip_url;
      zipLink.hidden = false;
    }
  }).catch(function (error) {
    var item = document.createElement("li");
    item.className = "result failed";
    item.textContent = error.message;
    results.appendChild(item);
  }).then(function () {
    button.textContent = originalLabel;
    button.disabled = state.files.length === 0;
  });
}

(function init() {
  var tabButtons = document.querySelectorAll(".tab");
  var i;
  for (i = 0; i < tabButtons.length; i += 1) {
    tabButtons[i].addEventListener("click", function (event) {
      activateTab(event.currentTarget.dataset.tab);
    });
  }

  var pickers = document.querySelectorAll("[data-pick]");
  for (i = 0; i < pickers.length; i += 1) {
    pickers[i].addEventListener("click", function (event) {
      byId(event.currentTarget.dataset.pick).click();
    });
  }

  Object.keys(TABS).forEach(function (tabName) {
    var input = byId(tabName + "-input");
    input.addEventListener("change", function () {
      addFiles(tabName, input.files);
      input.value = "";
    });

    var drop = document.querySelector("[data-drop='" + tabName + "']");
    ["dragenter", "dragover"].forEach(function (name) {
      drop.addEventListener(name, function (event) {
        event.preventDefault();
        drop.classList.add("is-over");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      drop.addEventListener(name, function (event) {
        event.preventDefault();
        drop.classList.remove("is-over");
      });
    });
    drop.addEventListener("drop", function (event) {
      addFiles(tabName, event.dataTransfer.files);
    });

    byId(tabName + "-run").addEventListener("click", function () { run(tabName); });
  });

  byId("merge-compress").addEventListener("change", function (event) {
    byId("merge-presets").hidden = !event.currentTarget.checked;
  });
}());
