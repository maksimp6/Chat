/* SPDX-License-Identifier: Apache-2.0
 * Adapted from Wikifunctions implementations Z10081@143512, Z12317@285821, Z10048@207179 and Z10019@216191.
 * Changes: descriptive names, formatting, and browser/CommonJS exports.
 * Source, original code and license: third_party/wikifunctions/.
 */
(function () {
  "use strict";

  // WIKIFUNCTIONS Z10079 / Z10081@143512 — НЕ РЕДАКТИРОВАТЬ.
  function trim(input) {
    return input.trim();
  }

  // WIKIFUNCTIONS Z12316 / Z12317@285821 — НЕ РЕДАКТИРОВАТЬ.
  function replaceRegex(pattern, replacement, input, flags) {
    return input.replace(RegExp(pattern, flags), replacement);
  }

  // WIKIFUNCTIONS Z10047 / Z10048@207179 — НЕ РЕДАКТИРОВАТЬ.
  function lowercase(input) {
    return input.toLowerCase();
  }

  // WIKIFUNCTIONS Z10018 / Z10019@216191 — НЕ РЕДАКТИРОВАТЬ.
  function uppercase(input) {
    return input.toUpperCase();
  }

  var functions = Object.freeze({
    trim: trim,
    replaceRegex: replaceRegex,
    lowercase: lowercase,
    uppercase: uppercase,
  });
  if (typeof module === "object" && module.exports) {
    module.exports = functions;
  } else {
    window.LogosWikifunctions = functions;
  }
})();
