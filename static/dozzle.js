(function () {
  function openDozzleLogs() {
    var base = window.__LOGOS_BASE_PATH || "";
    var path = (base ? window.LogosWikifunctions.replaceRegex("\\/$", "", base) : "") + "/logs/";
    window.open(path, "_blank", "noopener,noreferrer");
  }

  window.openDozzleLogs = openDozzleLogs;
})();
