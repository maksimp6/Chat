/* SPDX-License-Identifier: Apache-2.0
 * Adapted from Wikifunctions Z12317@285821.
 * Changes: descriptive names, formatting, and the browser export.
 * Source, original code and license: third_party/wikifunctions/.
 */
(function () {
  "use strict";

  function replaceRegex(pattern, replacement, input, flags) {
    return input.replace(RegExp(pattern, flags), replacement);
  }

  window.LogosWikifunctions = Object.freeze({ replaceRegex: replaceRegex });
})();
