# Browser parser dependencies

These pinned upstream libraries are served from the Logos checkout; the browser
does not contact a CDN. Versions, source tarballs and file hashes are recorded in
`packages.json`. Upstream MIT licenses are included beside each library.

- markdown-it 15.0.3: bundled as a classic browser script exposing `window.markdownit`.
  To reproduce, run `npm ci --ignore-scripts` followed by `npm run build` inside
  `markdown-it-15.0.3`. The checked-in lockfile also pins the build tool.
- KaTeX 0.19.1: the unmodified upstream browser build. Logos requests MathML
  output, using the browser's math layout without downloading KaTeX webfonts.
- markdown-it-texmath 1.0.0: the unmodified upstream delimiter plugin.

No npm installation or build is needed on the running Logos server.
