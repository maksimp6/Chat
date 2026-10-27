# Vendored emulator dependency

`ipaddr.js` is the unmodified CommonJS source from
[`whitequark/ipaddr.js` v2.2.0](https://github.com/whitequark/ipaddr.js/tree/v2.2.0).
The source is included locally because the emulator is launched directly with Node
and does not require an npm installation step.

- Source: `https://raw.githubusercontent.com/whitequark/ipaddr.js/v2.2.0/lib/ipaddr.js`
- SHA-256: `75c602c8834cc9e1bcdb7d1d2eaadbbd5ba0e19660d9ea3f450dbf17f2ec8495`
- License: MIT, retained in `ipaddr.LICENSE`.

The caller uses Node's `net.isIP` first, so the dependency classifies only IP
literals. DNS names retain separate localhost and `.local` policy checks.
