/* SPDX-License-Identifier: Apache-2.0
 * Adapted from Wikifunctions Z10081@143512 and Z12317@285821.
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

  var functions = Object.freeze({ trim: trim, replaceRegex: replaceRegex });
  if (typeof module === "object" && module.exports) {
    module.exports = functions;
  } else {
    window.LogosWikifunctions = functions;
  }
})();
