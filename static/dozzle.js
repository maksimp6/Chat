(function () {
  function openDozzleLogs() {
    var base = window.__ALICE_BASE_PATH || "";
    var path = (base ? base.replace(/\/$/, "") : "") + "/logs/";
    window.open(path, "_blank", "noopener,noreferrer");
  }

  window.openDozzleLogs = openDozzleLogs;
})();
