(function () {
  var BASE = window.DRAMA_BASE || "";
  var POLL_MS = 10000;
  var FILTERS = ["all", "passed", "failed", "progress", "liked"];

  var list = document.getElementById("video-list");
  var emptyNote = document.getElementById("empty-note");
  var loadError = document.getElementById("load-error");
  var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab"));
  var dialog = document.getElementById("player-dialog");
  var dialogVideo = document.getElementById("dialog-video");

  var projects = [];
  var lastJson = "";
  var params = new URLSearchParams(location.search);
  var newId = params.get("new"); // set right after Generate: highlight that project once
  var filter = FILTERS.indexOf(location.hash.slice(1)) >= 0 ? location.hash.slice(1) : "all";

  function category(p) {
    if (p.status === "succeeded") return "passed";
    if (p.status === "failed") return "failed";
    return "progress"; // submitting / queued / running
  }

  function rememberedIds() {
    try {
      return JSON.parse(localStorage.getItem("drama_jobs") || "[]").map(function (j) { return j.id; });
    } catch (e) { return []; }
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function formatSeconds(s) {
    return Math.floor(s / 60) + "m " + ("0" + Math.round(s % 60)).slice(-2) + "s";
  }

  function formatMeta(p) {
    var parts = [new Date(p.created_at).toLocaleString()];
    if (p.video) {
      if (p.video.duration_s) parts.push(p.video.duration_s + " s");
      if (p.video.resolution) parts.push(p.video.resolution);
      if (p.video.bytes) parts.push((p.video.bytes / 1048576).toFixed(1) + " MB");
    }
    if (p.status === "succeeded" && p.ark_total_s) parts.push("made in " + formatSeconds(p.ark_total_s));
    return parts.join(" · ");
  }

  var STAGES = {
    image: "Step 1: creating the character image (Seedream)",
    submit: "Step 2: sending the video request (Seedance)",
    task: "Step 2: generating the video (Seedance)",
    timeout: "Waiting for the result"
  };

  var PROGRESS_TEXT = {
    image_generating: "Step 1 of 2: creating your character image… ",
    submitting: "Step 2 of 2: sending your scene to the studio… ",
    queued: "Step 2 of 2: waiting in the studio queue… ",
    running: "Step 2 of 2: filming your drama, this can take up to about 10 minutes. "
  };

  function detailRow(dl, label, value) {
    if (value == null || value === "") return;
    dl.appendChild(el("dt", null, label));
    dl.appendChild(el("dd", null, String(value)));
  }

  function failureBlock(p) {
    var box = el("div", "reason");
    box.appendChild(el("p", "reason-text", p.error || "Video generation failed."));
    if (p.detail) {
      var details = el("details", "reason-details");
      details.appendChild(el("summary", null, "Technical details"));
      var dl = el("dl");
      detailRow(dl, "Failed at", STAGES[p.detail.stage] || p.detail.stage);
      detailRow(dl, "Error code", p.detail.code);
      detailRow(dl, "HTTP status", p.detail.status_code);
      detailRow(dl, "Request ID", p.detail.request_id);
      detailRow(dl, "Message", p.detail.message);
      details.appendChild(dl);
      box.appendChild(details);
    }
    return box;
  }

  function openPlayer(url) {
    dialogVideo.src = url;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
    dialogVideo.play().catch(function () { /* autoplay blocked: the user presses play */ });
  }

  function closePlayer() {
    dialogVideo.pause();
    dialogVideo.removeAttribute("src");
    dialogVideo.load();
    if (dialog.open) dialog.close();
  }

  function setLiked(p, liked) {
    p.liked = liked; // optimistic: update the row now, undo if the server says no
    lastJson = "";
    render();
    fetch(BASE + "/api/projects/" + p.id + "/like", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ liked: liked })
    })
      .then(function (res) {
        var type = res.headers.get("Content-Type") || "";
        if (!res.ok || type.indexOf("json") < 0) throw new Error("Could not save the like. Reload the page and try again.");
        loadError.hidden = true;
        lastJson = ""; // a list poll that raced this request must not keep the old state
      })
      .then(function () {
        if (lastJson === "") load();
      })
      .catch(function (err) {
        p.liked = !liked;
        render();
        loadError.textContent = err.message;
        loadError.hidden = false;
      });
  }

  function likeLink(p) {
    var btn = el("button", "row-link like-link" + (p.liked ? " like-on" : ""), p.liked ? "♥ Unlike" : "♡ Like");
    btn.type = "button";
    btn.setAttribute("aria-pressed", p.liked ? "true" : "false");
    btn.addEventListener("click", function () { setLiked(p, !p.liked); });
    return btn;
  }

  var EMAIL_BADGES = {
    sent: ["✉ Sent", "badge-done", "The email with the download link was sent"],
    failed: ["✉ Not sent", "badge-failed", "The email could not be sent"],
    queued: ["✉ Sending", "", "The email is being sent"]
  };

  function emailBadge(p) {
    if (!p.contact) return null;
    var status = p.contact.email_status || (category(p) === "progress" ? "pending" : null);
    var spec = status === "pending" ? ["✉ Pending", "", "An email will be sent when the video is ready"] : EMAIL_BADGES[status];
    if (!spec) return null;
    var badge = el("span", "badge " + spec[1], spec[0]);
    badge.title = spec[2];
    return badge;
  }

  function contactLine(p, cat) {
    var c = p.contact;
    var line = el("p", "small row-contact");
    line.appendChild(el("span", "contact-who", "✉ " + c.name + " · "));
    var mail = el("a", null, c.email);
    mail.href = "mailto:" + c.email;
    line.appendChild(mail);
    var state;
    if (c.email_status === "sent") {
      state = el("span", "contact-sent", " · ✓ Email sent at " + new Date(c.email_at).toLocaleString([], { dateStyle: "short", timeStyle: "short" }));
    } else if (c.email_status === "failed") {
      state = el("span", "contact-failed", " · ✗ Email not sent" + (c.email_error ? ": " + c.email_error.slice(0, 90) : ""));
      if (c.email_error) state.title = c.email_error;
    } else if (c.email_status === "queued") {
      state = el("span", "muted", " · Sending email…");
    } else if (cat === "progress") {
      state = el("span", "muted", " · Email when ready");
    } else if (cat === "failed") {
      state = el("span", "muted", " · No email (generation failed)");
    }
    if (state) line.appendChild(state);
    return line;
  }

  function mediaColumn(p, cat) {
    var box = el("div", "row-media");
    if (cat === "passed" && p.video) {
      var thumb = el("button", "thumb");
      thumb.type = "button";
      thumb.setAttribute("aria-label", "Play video");
      var v = document.createElement("video");
      v.muted = true;
      v.playsInline = true;
      v.preload = "metadata";
      v.src = p.video.url + "#t=0.1"; // #t shows the first frame instead of a black box
      thumb.appendChild(v);
      thumb.appendChild(el("span", "thumb-play", "▶"));
      thumb.addEventListener("click", function () { openPlayer(p.video.url); });
      box.appendChild(thumb);
    } else if (cat === "passed") {
      box.appendChild(el("div", "tile tile-muted", "Gone"));
    } else if (cat === "failed") {
      box.appendChild(el("div", "tile tile-failed", "!"));
    } else {
      var tile = el("div", "tile");
      tile.appendChild(el("div", "spinner spinner-small"));
      box.appendChild(tile);
    }
    return box;
  }

  function row(p) {
    var cat = category(p);
    var li = el("li", "row row-" + cat);
    li.dataset.id = p.id;
    li.appendChild(mediaColumn(p, cat));

    var main = el("div", "row-main");
    var head = el("div", "row-head");
    head.appendChild(el("strong", "row-title", p.genre_title));
    head.appendChild(el("span", "badge" + (cat === "passed" ? " badge-done" : cat === "failed" ? " badge-failed" : ""),
      cat === "passed" ? "Passed" : cat === "failed" ? "Failed" : "In progress"));
    if (p.liked) head.appendChild(el("span", "badge badge-liked", "♥ Liked"));
    var mailBadge = emailBadge(p);
    if (mailBadge) head.appendChild(mailBadge);
    main.appendChild(head);
    main.appendChild(el("p", "muted small row-meta", formatMeta(p)));
    if (p.contact) main.appendChild(contactLine(p, cat));
    if (p.plot) main.appendChild(el("p", "small row-plot", "“" + p.plot + "”"));

    if (cat === "failed") {
      main.appendChild(failureBlock(p));
      var retry = el("a", "row-link", "Try again");
      retry.href = window.CREATE_URL;
      main.appendChild(el("div", "row-actions")).appendChild(retry);
    } else if (cat === "progress") {
      var status = el("p", "progress-line", PROGRESS_TEXT[p.status] || PROGRESS_TEXT.running);
      var elapsed = el("span", "muted elapsed");
      elapsed.dataset.created = p.created_at;
      status.appendChild(elapsed);
      main.appendChild(status);
    } else if (p.video) {
      var actions = el("div", "row-actions");
      var play = el("button", "row-link", "Play");
      play.type = "button";
      play.addEventListener("click", function () { openPlayer(p.video.url); });
      var dl = el("a", "row-link", "Download");
      dl.href = p.video.url;
      dl.download = "drama.mp4";
      actions.appendChild(play);
      actions.appendChild(dl);
      actions.appendChild(likeLink(p));
      main.appendChild(actions);
    } else {
      main.appendChild(el("p", "small muted", "The video file is no longer available on the server."));
    }

    li.appendChild(main);
    return li;
  }

  var EMPTY = {
    all: "No generations yet.",
    passed: "No passed videos yet.",
    failed: "No failed generations. 🎉",
    progress: "Nothing is being generated right now.",
    liked: "No liked videos yet. Press ♡ Like on a video to add it here."
  };

  function tickElapsed() {
    Array.prototype.forEach.call(document.querySelectorAll(".elapsed"), function (node) {
      var s = Math.max(0, (Date.now() - new Date(node.dataset.created).getTime()) / 1000);
      node.textContent = "Elapsed " + Math.floor(s / 60) + ":" + ("0" + Math.floor(s % 60)).slice(-2);
    });
  }

  function render() {
    var counts = { all: projects.length, passed: 0, failed: 0, progress: 0, liked: 0 };
    projects.forEach(function (p) {
      counts[category(p)] += 1;
      if (p.liked) counts.liked += 1;
    });
    Object.keys(counts).forEach(function (key) {
      document.querySelector('[data-count="' + key + '"]').textContent = counts[key];
    });
    tabs.forEach(function (t) {
      var active = t.dataset.filter === filter;
      t.classList.toggle("tab-active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
    });

    list.textContent = "";
    var shown = projects.filter(function (p) {
      if (filter === "liked") return p.liked;
      return filter === "all" || category(p) === filter;
    });
    shown.forEach(function (p) { list.appendChild(row(p)); });

    emptyNote.hidden = shown.length > 0;
    if (!shown.length) {
      emptyNote.textContent = "";
      emptyNote.appendChild(document.createTextNode(EMPTY[filter] + " "));
      if (filter === "passed" || filter === "all") {
        var a = el("a", null, "Create a drama →");
        a.href = window.CREATE_URL;
        emptyNote.appendChild(a);
      }
    }
    tickElapsed();

    if (newId) {
      var fresh = list.querySelector('[data-id="' + newId + '"]');
      if (fresh) {
        fresh.classList.add("row-new");
        fresh.scrollIntoView({ block: "nearest" });
        history.replaceState(null, "", location.pathname + "#" + filter); // highlight once, not on every reload
        newId = null;
      }
    }
  }

  function load() {
    var url = BASE + "/api/projects?ids=" + encodeURIComponent(rememberedIds().join(","));
    return fetch(url)
      .then(function (res) {
        var type = res.headers.get("Content-Type") || "";
        // An expired login session redirects to the sign-in page (HTML) instead of returning JSON.
        if (!res.ok || type.indexOf("json") < 0) throw new Error("Could not load your videos. Reload the page and sign in again if needed.");
        return res.json();
      })
      .then(function (data) {
        loadError.hidden = true;
        var json = JSON.stringify(data);
        if (json === lastJson) return; // nothing changed: keep the DOM (and the loaded thumbnails) as is
        lastJson = json;
        projects = data;
        render();
      })
      .catch(function (err) {
        loadError.textContent = err.message;
        loadError.hidden = false;
      });
  }

  // In-progress projects only advance when someone polls them, so this page does it too.
  function pollRunning() {
    var running = projects.filter(function (p) { return category(p) === "progress"; });
    if (!running.length) return;
    Promise.all(running.map(function (p) {
      return fetch(BASE + "/api/jobs/" + p.id).catch(function () {});
    })).then(load);
  }

  tabs.forEach(function (t) {
    t.addEventListener("click", function () {
      filter = t.dataset.filter;
      history.replaceState(null, "", location.pathname + "#" + filter);
      render();
    });
  });

  document.getElementById("player-close").addEventListener("click", closePlayer);
  dialog.addEventListener("click", function (e) { if (e.target === dialog) closePlayer(); }); // click on the backdrop
  dialog.addEventListener("close", function () { dialogVideo.pause(); });

  load().then(pollRunning); // an in-progress project is checked straight away, then every 10 s
  setInterval(pollRunning, POLL_MS);
  setInterval(tickElapsed, 1000);
})();
