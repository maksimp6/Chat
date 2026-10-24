(function () {
  "use strict";

  function alignLastRow() {
    const header = document.getElementById("header");
    const actions = header && header.querySelector(".header-actions");
    if (!actions) return;

    const buttons = Array.from(actions.children).filter(
      (child) => child.classList && child.classList.contains("header-btn"),
    );
    buttons.forEach((button) => button.style.removeProperty("grid-column-start"));
    if (!buttons.length) return;

    const styles = window.getComputedStyle(actions);
    const gap = parseFloat(styles.columnGap) || 0;
    const itemWidth = buttons[0].getBoundingClientRect().width || 0;
    if (!itemWidth) return;

    const columns = Math.max(1, Math.floor((actions.clientWidth + gap) / (itemWidth + gap)));
    actions.style.gridTemplateColumns = `repeat(${columns}, ${itemWidth}px)`;

    const remainder = buttons.length % columns;
    if (buttons.length <= columns || remainder === 0) return;

    buttons[buttons.length - remainder].style.gridColumnStart = String(columns - remainder + 1);
  }

  function schedule() {
    window.requestAnimationFrame(alignLastRow);
  }

  function init() {
    schedule();
    const header = document.getElementById("header");
    const actions = header && header.querySelector(".header-actions");
    if (!actions) return;
    window.addEventListener("resize", schedule);
    if (typeof ResizeObserver === "function") new ResizeObserver(schedule).observe(actions);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
