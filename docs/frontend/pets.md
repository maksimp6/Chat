# Chat companions

`static/pets.js` owns the companion strip between the chat and composer. The initial
catalog contains **Плюш**, the owner's custom plush dragon. Settings → Оформление →
Питомцы switches between Плюш and Без питомца. The `alice_pet` device preference is
independent of conversation/model settings and never enters an API prompt. Плюш is
the default; unsupported stored IDs safely disable the companion.

The public `window.AlicePets` API exposes `catalog`, `getSelected`, `select`, `begin`,
`reset`, and lifecycle `init/start/stop/destroy`. `begin("running" | "waiting")` returns
an idempotent completion callback accepting `success`, `failed`, or no outcome.
Concurrent requests keep the working animation until all work finishes. Approval
cards own a waiting callback. Conversation changes invalidate old callbacks.

The renderer uses the Core scheduler, cancels pending work when disabled/hidden,
pauses on pagehide, resumes on pageshow, respects capability revocation and reduced
motion, and leaves a static frame if scheduling is unavailable. The strip occupies
normal layout space and collapses in short viewports; it never overlays the composer.

`static/pets/plush.png` is the owner's ChatGPT Work custom pet export, copied without
image changes: 1536 × 2288 RGBA, 8 columns × 11 rows, 192 × 208 pixels per frame.
Source pet ID: `pet_6ac08484927c8191a9c18fee75239120`.
SHA-256: `5b40225010fcbfe82cd7190b8c4a1c1f5eeb27a282dc995e4c3d02b1cf7f5558`.
No ChatGPT authentication or expiring download URL is needed at runtime.

Rows used: idle 0 (6 frames), waving 3 (4), jumping 4 (5), failed 5 (8), waiting 6 (6),
running 7 (6). The unused gait/review/look rows remain in the original atlas. For a
future catalog entry, add its owned local atlas and per-pet render metadata; do not
copy short-lived asset URLs or assume another pet has this layout.

Validation: `node tests/test_pets.js` exercises animation progression, concurrency,
real chat success/error/approval paths, stale responses, settings persistence,
storage denial, reduced motion, visibility, revocation, and atlas dimensions.
