(function () {
  var MAX_EDGE = 1280;
  var MIN_EDGE = 300; // Seedance needs reference images of at least 300px
  var JPEG_QUALITY = 0.8;
  var BASE = window.DRAMA_BASE || "";

  var openCamBtn = document.getElementById("photo-open-cam");
  var closeCamBtn = document.getElementById("photo-close-cam");
  var captureBtn = document.getElementById("photo-capture-btn");
  var takeBtn = document.getElementById("photo-take-btn");
  var galleryBtn = document.getElementById("photo-gallery-btn");
  var takeInput = document.getElementById("photo-take-input");
  var cameraBox = document.getElementById("photo-camera");
  var video = document.getElementById("photo-video");
  var preview = document.getElementById("photo-preview");
  var statusEl = document.getElementById("photo-status");
  var picker = document.getElementById("photo-picker");
  var pickerGrid = document.getElementById("picker-grid");
  var pickerNote = document.getElementById("picker-note");

  var plotEl = document.getElementById("plot");
  var consentEl = document.getElementById("consent");
  var codeEl = document.getElementById("access-code");
  var nameEl = document.getElementById("contact-name");
  var emailEl = document.getElementById("contact-email");
  var EMAIL_RE = /^[^\s@<>"',;]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/;
  var generateBtn = document.getElementById("generate-btn");
  var generateStatus = document.getElementById("generate-status");

  var stream = null;
  var photoDataUrl = null;
  var submitting = false;

  function setStatus(el, text, kind) {
    el.textContent = text;
    el.className = "photo-status" + (kind ? " photo-status-" + kind : "");
  }

  function contactOk() {
    return nameEl.value.trim() !== "" && EMAIL_RE.test(emailEl.value.trim());
  }

  function updateGenerateButton() {
    generateBtn.disabled = submitting || !photoDataUrl || !consentEl.checked || !contactOk();
  }

  // Remember the contact details in this browser only, so a returning visitor needn't retype them.
  try {
    var saved = JSON.parse(localStorage.getItem("drama_contact") || "{}");
    if (saved.name) nameEl.value = saved.name;
    if (saved.email) emailEl.value = saved.email;
  } catch (e) { /* storage blocked */ }

  function rememberContact() {
    try {
      localStorage.setItem("drama_contact", JSON.stringify({ name: nameEl.value.trim(), email: emailEl.value.trim() }));
    } catch (e) { /* storage blocked */ }
  }

  // getUserMedia only works on HTTPS or localhost. On plain HTTP the
  // "Take photo / upload" button still opens the phone's native camera.
  var canUseCamera = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  if (!canUseCamera) {
    openCamBtn.disabled = true;
    openCamBtn.title = "This page is not HTTPS, so the browser will not open the camera directly";
    setStatus(statusEl, "This connection is not HTTPS, so the live camera is unavailable. Use “Take photo / upload” to use your device's camera app.");
  }

  function stopCamera() {
    if (stream) {
      stream.getTracks().forEach(function (t) { t.stop(); });
      stream = null;
    }
    video.srcObject = null;
    cameraBox.hidden = true;
  }

  function openCamera() {
    setStatus(statusEl, "Opening camera…");
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: { ideal: "user" } }, audio: false })
      .then(function (s) {
        stream = s;
        video.srcObject = s;
        cameraBox.hidden = false;
        setStatus(statusEl, "Look at the camera and tap “Capture”.");
      })
      .catch(function (err) {
        var denied = err && (err.name === "NotAllowedError" || err.name === "SecurityError");
        var missing = err && (err.name === "NotFoundError" || err.name === "OverconstrainedError");
        setStatus(
          statusEl,
          denied ? "Camera permission was denied. Please allow camera access in your browser settings."
            : missing ? "No camera was found."
            : "Could not open the camera: " + (err && err.message ? err.message : err),
          "error"
        );
      });
  }

  // Draw any image/video source onto a canvas, scaled down so the upload stays small.
  function toDataUrl(source, width, height) {
    var scale = Math.min(1, MAX_EDGE / Math.max(width, height));
    var canvas = document.createElement("canvas");
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    var ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", JPEG_QUALITY);
  }

  function setPhoto(dataUrl, width, height) {
    if (Math.min(width, height) < MIN_EDGE) {
      setStatus(statusEl, "This photo is too small. Please use one at least " + MIN_EDGE + "px on each side.", "error");
      return;
    }
    photoDataUrl = dataUrl;
    preview.src = dataUrl;
    preview.hidden = false;
    setStatus(statusEl, "Photo ready. You can retake it any time.", "done");
    updateGenerateButton();
  }

  function captureFromVideo() {
    if (!video.videoWidth) {
      setStatus(statusEl, "The camera isn't ready yet. Wait a moment and try again.", "error");
      return;
    }
    var w = video.videoWidth, h = video.videoHeight;
    var dataUrl = toDataUrl(video, w, h);
    stopCamera();
    setPhoto(dataUrl, w, h);
  }

  function handleFile(file) {
    if (!file) return;
    if (!/^image\//.test(file.type)) {
      setStatus(statusEl, "Please choose an image file.", "error");
      return;
    }
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function () {
      var dataUrl = toDataUrl(img, img.naturalWidth, img.naturalHeight);
      URL.revokeObjectURL(url);
      setPhoto(dataUrl, img.naturalWidth, img.naturalHeight);
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      setStatus(statusEl, "That image couldn't be read. Please try another one.", "error");
    };
    img.src = url;
  }

  function rememberedIds() {
    try {
      return JSON.parse(localStorage.getItem("drama_jobs") || "[]").map(function (j) { return j.id; });
    } catch (e) { return []; }
  }

  function closePicker() {
    if (picker.open) picker.close();
  }

  // "Choose from gallery": photos stored in earlier projects' assets/, each distinct photo listed once.
  function openPicker() {
    pickerGrid.textContent = "";
    pickerNote.textContent = "Loading…";
    pickerNote.hidden = false;
    if (typeof picker.showModal === "function") picker.showModal(); else picker.setAttribute("open", "");

    fetch(BASE + "/api/photos?ids=" + encodeURIComponent(rememberedIds().join(",")))
      .then(function (res) {
        var type = res.headers.get("Content-Type") || "";
        // An expired login session redirects to the sign-in page (HTML) instead of returning JSON.
        if (!res.ok || type.indexOf("json") < 0) throw new Error("Could not load the photos. Reload the page and sign in again if needed.");
        return res.json();
      })
      .then(function (photos) {
        pickerNote.hidden = photos.length > 0;
        pickerNote.textContent = "No photos yet. Take or upload one first, and it will appear here next time.";
        photos.forEach(function (p) { pickerGrid.appendChild(pickerItem(p)); });
      })
      .catch(function (err) {
        pickerNote.textContent = err.message;
        pickerNote.hidden = false;
      });
  }

  function pickerItem(p) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "picker-item";

    var img = document.createElement("img");
    img.src = p.url;
    img.loading = "lazy";
    img.alt = "Photo from " + new Date(p.created_at).toLocaleString();
    button.appendChild(img);

    var meta = document.createElement("span");
    meta.className = "picker-meta";
    meta.textContent = new Date(p.created_at).toLocaleDateString() + (p.uses > 1 ? " · used " + p.uses + "×" : "");
    button.appendChild(meta);

    button.addEventListener("click", function () { choosePhoto(p); });
    return button;
  }

  // The chosen photo goes through the same path as a camera or upload photo (size check, resize, preview).
  function choosePhoto(p) {
    closePicker();
    setStatus(statusEl, "Loading the photo…");
    fetch(p.url)
      .then(function (res) {
        if (!res.ok) throw new Error("The photo could not be loaded (" + res.status + ").");
        return res.blob();
      })
      .then(handleFile)
      .catch(function (err) { setStatus(statusEl, err.message, "error"); });
  }

  function rememberJob(id, genre) {
    try {
      var list = JSON.parse(localStorage.getItem("drama_jobs") || "[]");
      list.unshift({ id: id, genre: genre, at: new Date().toISOString() });
      localStorage.setItem("drama_jobs", JSON.stringify(list.slice(0, 50)));
    } catch (e) { /* storage blocked: the watch page still works from the link */ }
  }

  function generate() {
    var genreInput = document.querySelector("input[name=genre]:checked");
    if (!photoDataUrl || !genreInput) return;
    var genre = genreInput.value;

    rememberContact();
    submitting = true;
    updateGenerateButton();
    setStatus(generateStatus, "Uploading your photo and sending it to the studio…");

    fetch(BASE + "/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image: photoDataUrl,
        genre: genre,
        plot: plotEl.value,
        consent: consentEl.checked,
        name: nameEl.value.trim(),
        email: emailEl.value.trim(),
        access_code: codeEl ? codeEl.value : ""
      })
    })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) {
            // A project exists even when BytePlus rejected the request: show its failure in the list.
            if (data.job_id) {
              rememberJob(data.job_id, genre);
              window.location.href = BASE + "/videos?new=" + data.job_id + "#all";
            }
            throw new Error(res.status === 413 ? "The photo is too large to upload." : data.error || "Request failed (" + res.status + ")");
          }
          return data;
        });
      })
      .then(function (data) {
        // An expired login session makes fetch follow a redirect to the sign-in page, which is not JSON.
        if (!data.job_id) throw new Error("Your session may have expired. Reload the page and sign in again.");
        rememberJob(data.job_id, genre);
        // The result (progress, then the video or the failure reason) shows in the My videos list.
        window.location.href = BASE + "/videos?new=" + data.job_id + "#all";
      })
      .catch(function (err) {
        setStatus(generateStatus, err.message, "error");
        submitting = false;
        updateGenerateButton();
      });
  }

  openCamBtn.addEventListener("click", openCamera);
  closeCamBtn.addEventListener("click", function () {
    stopCamera();
    setStatus(statusEl, "");
  });
  captureBtn.addEventListener("click", captureFromVideo);
  takeBtn.addEventListener("click", function () { takeInput.click(); });
  galleryBtn.addEventListener("click", openPicker);
  document.getElementById("picker-close").addEventListener("click", closePicker);
  picker.addEventListener("click", function (e) { if (e.target === picker) closePicker(); }); // backdrop click
  consentEl.addEventListener("change", updateGenerateButton);
  nameEl.addEventListener("input", updateGenerateButton);
  emailEl.addEventListener("input", updateGenerateButton);
  generateBtn.addEventListener("click", generate);

  takeInput.addEventListener("change", function () {
    var file = takeInput.files && takeInput.files[0];
    takeInput.value = "";
    handleFile(file);
  });

  window.addEventListener("pagehide", stopCamera);
})();
