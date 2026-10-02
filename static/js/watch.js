(function () {
  var POLL_MS = 8000;
  var BASE = window.DRAMA_BASE || "";
  var root = document.getElementById("watch");
  var jobId = root.dataset.jobId;

  var progress = document.getElementById("progress");
  var progressText = document.getElementById("progress-text");
  var elapsedEl = document.getElementById("progress-elapsed");
  var result = document.getElementById("result");
  var player = document.getElementById("player");
  var downloadLink = document.getElementById("download-link");
  var errorBox = document.getElementById("error-box");
  var errorText = document.getElementById("error-text");

  var startedAt = Date.now();
  var timer = null;

  var MESSAGES = {
    image_generating: "Step 1 of 2: creating your character image…",
    submitting: "Step 2 of 2: sending your scene to the studio…",
    queued: "Step 2 of 2: waiting in the studio queue…",
    running: "Step 2 of 2: filming your drama. This can take up to about 10 minutes…"
  };

  function tickElapsed() {
    var s = Math.floor((Date.now() - startedAt) / 1000);
    elapsedEl.textContent = "Elapsed " + Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2);
  }

  function showError(message) {
    clearInterval(timer);
    progress.hidden = true;
    errorText.textContent = message;
    errorBox.hidden = false;
  }

  function poll() {
    fetch(BASE + "/api/jobs/" + jobId)
      .then(function (res) {
        if (res.status === 404) throw new Error("This video could not be found.");
        return res.json();
      })
      .then(function (job) {
        if (job.created_at) startedAt = new Date(job.created_at).getTime();
        if (job.status === "succeeded") {
          clearInterval(timer);
          progress.hidden = true;
          player.src = job.video_url;
          downloadLink.href = job.video_url;
          result.hidden = false;
        } else if (job.status === "failed") {
          showError(job.error || "Video generation failed.");
        } else {
          progressText.textContent = MESSAGES[job.status] || MESSAGES.running;
        }
      })
      .catch(function (err) {
        // A dropped connection shouldn't abort the wait; only a missing job is final.
        if (/could not be found/.test(err.message)) showError(err.message);
      });
  }

  poll();
  timer = setInterval(poll, POLL_MS);
  setInterval(tickElapsed, 1000);
})();
