(function () {
  "use strict";

  var core = window.AliceCoreAPI;
  if (!core) return;
  var context = core.module.register({
    id: "pets",
    version: "1.0.0",
    apiVersion: "1",
    dependencies: [],
    capabilities: ["storage.read", "storage.write"],
  });
  var catalog = Object.freeze([
    Object.freeze({ id: "plush", name: "Плюш", description: "Плюшевый дракон" }),
  ]);
  var animations = {
    idle: { row: 0, durations: [280, 110, 110, 140, 140, 320], label: "Рядом" },
    waving: { row: 3, durations: [140, 140, 140, 280], label: "Привет!", once: true },
    jumping: { row: 4, durations: [140, 140, 140, 140, 280], label: "Ответ готов", once: true },
    failed: {
      row: 5,
      durations: [140, 140, 140, 140, 140, 140, 140, 240],
      label: "Что-то пошло не так",
      once: true,
    },
    waiting: { row: 6, durations: [150, 150, 150, 150, 150, 260], label: "Жду подтверждения" },
    running: { row: 7, durations: [120, 120, 120, 120, 120, 220], label: "Думаю…" },
  };
  var storageKey = "alice_pet";
  var selected = "plush";
  var root, button, sprite, label, pending, unsubscribe;
  var motion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var state = "idle",
    frame = 0,
    active = false,
    epoch = 0,
    working = 0,
    waiting = 0;

  function cancelFrame() {
    if (pending) pending.cancel();
    pending = null;
  }

  function baseline() {
    return working ? "running" : waiting ? "waiting" : "idle";
  }

  function paint() {
    if (!sprite) return;
    var animation = animations[state];
    sprite.style.backgroundPosition = -frame * 60 + "px " + -animation.row * 65 + "px";
    root.dataset.state = state;
    label.textContent = "Плюш · " + animation.label;
    button.setAttribute("aria-label", "Плюш: " + animation.label + ". Поприветствовать");
  }

  function schedule() {
    cancelFrame();
    if (!active || !root || root.hidden || document.hidden || context.revoke.isRevoked()) return;
    if (motion && motion.matches && !animations[state].once) return;
    try {
      pending = core.scheduler.defer(
        advance,
        motion && motion.matches ? 2000 : animations[state].durations[frame],
      );
    } catch (_) {
      // Keep a static companion when the browser cannot schedule frames.
    }
  }

  function advance() {
    pending = null;
    if (!active || document.hidden || context.revoke.isRevoked()) return;
    var animation = animations[state];
    if (motion && motion.matches) {
      setState(baseline());
      return;
    }
    frame += 1;
    if (frame >= animation.durations.length) {
      if (animation.once) {
        setState(baseline());
        return;
      }
      frame = 0;
    }
    paint();
    schedule();
  }

  function setState(next) {
    state = next;
    frame = 0;
    paint();
    schedule();
  }

  function select(id, persist) {
    selected = catalog.some(function (pet) {
      return pet.id === id;
    })
      ? id
      : "none";
    if (persist !== false) {
      try {
        context.security.require("storage.write");
        localStorage.setItem(storageKey, selected);
      } catch (_) {}
    }
    if (root) {
      root.hidden = selected === "none" || !active || context.revoke.isRevoked();
      sprite.style.backgroundImage = root.hidden
        ? "none"
        : 'url("' + root.dataset.staticRoot + '/pets/plush.png")';
    }
    setState(baseline());
    return selected;
  }

  function begin(kind) {
    var generation = epoch;
    var done = false;
    var approval = kind === "waiting";
    if (approval) waiting += 1;
    else working += 1;
    setState(baseline());
    return function (outcome) {
      if (done || generation !== epoch) return;
      done = true;
      if (approval) waiting = Math.max(0, waiting - 1);
      else working = Math.max(0, working - 1);
      setState(
        working || waiting
          ? baseline()
          : outcome === "failed"
            ? "failed"
            : outcome === "success"
              ? "jumping"
              : "idle",
      );
    };
  }

  function reset() {
    epoch += 1;
    working = 0;
    waiting = 0;
    setState("idle");
  }

  function greet() {
    if (!working && !waiting) setState("waving");
  }

  function refreshMotion() {
    frame = 0;
    paint();
    schedule();
  }

  function onStorage(event) {
    if (event.key === storageKey || event.key === null) select(event.newValue || "plush", false);
  }

  function start() {
    if (!root || context.revoke.isRevoked()) return;
    active = true;
    select(selected, false);
  }

  function stop() {
    active = false;
    cancelFrame();
    if (root) root.hidden = true;
  }

  function destroy() {
    stop();
    epoch += 1;
    if (button) button.removeEventListener("click", greet);
    document.removeEventListener("visibilitychange", refreshMotion);
    window.removeEventListener("storage", onStorage);
    window.removeEventListener("pagehide", stop);
    window.removeEventListener("pageshow", start);
    if (motion && motion.removeEventListener) motion.removeEventListener("change", refreshMotion);
    if (unsubscribe) unsubscribe();
    root = null;
    sprite = null;
    button = null;
    label = null;
    working = 0;
    waiting = 0;
  }

  function init() {
    if (root) return;
    root = document.getElementById("alice-pet");
    if (!root) return;
    button = root.querySelector(".alice-pet-button");
    sprite = root.querySelector(".alice-pet-sprite");
    label = root.querySelector(".alice-pet-label");
    try {
      context.security.require("storage.read");
      selected = localStorage.getItem(storageKey) || "plush";
    } catch (_) {}
    button.addEventListener("click", greet);
    document.addEventListener("visibilitychange", refreshMotion);
    window.addEventListener("storage", onStorage);
    window.addEventListener("pagehide", stop);
    window.addEventListener("pageshow", start);
    if (motion && motion.addEventListener) motion.addEventListener("change", refreshMotion);
    unsubscribe = core.status.subscribe(function () {
      if (context.revoke.isRevoked()) stop();
    });
    start();
  }

  window.AlicePets = Object.freeze({
    catalog: catalog,
    getSelected: function () {
      return selected;
    },
    select: select,
    begin: begin,
    reset: reset,
    init: init,
    start: start,
    stop: stop,
    destroy: destroy,
  });
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
