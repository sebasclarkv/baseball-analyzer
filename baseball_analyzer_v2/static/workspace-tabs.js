const tabs = [
  { key: "prep", hash: "#previa", button: "tab-prep", panel: "workspace-prep" },
  { key: "live", hash: "#en-vivo", button: "tab-live", panel: "workspace-live" },
];
let active;

export function workspaceIsActive(key) {
  return active === key;
}

function renderWorkspace(key) {
  if (active === key) return;
  const previous = tabs.find((tab) => tab.key === active);
  const focusedContentIsLeaving = previous && document.getElementById(previous.panel).contains(document.activeElement);
  active = key;
  for (const tab of tabs) {
    const current = tab.key === key;
    const button = document.getElementById(tab.button);
    button.setAttribute("aria-selected", String(current));
    button.tabIndex = current ? 0 : -1;
    document.getElementById(tab.panel).hidden = !current;
  }
  if (focusedContentIsLeaving) {
    document.getElementById(tabs.find((tab) => tab.key === key).button).focus();
  }
  window.dispatchEvent(new CustomEvent("diamondiq:workspace", { detail: { key } }));
}

export function showWorkspace(key) {
  const tab = tabs.find((item) => item.key === key);
  if (!tab) return;
  if (location.hash !== tab.hash) history.pushState(null, "", tab.hash);
  renderWorkspace(key);
}

export function initWorkspaceTabs() {
  const syncLocation = () => {
    const tab = tabs.find((item) => item.hash === location.hash);
    if (tab) renderWorkspace(tab.key);
    else {
      const anchor = document.getElementById(location.hash.slice(1));
      const parent = tabs.find((item) => document.getElementById(item.panel).contains(anchor));
      if (parent) renderWorkspace(parent.key);
      else if (!active || !location.hash) renderWorkspace("prep");
    }
  };

  tabs.forEach((tab, index) => {
    const button = document.getElementById(tab.button);
    button.addEventListener("click", () => showWorkspace(tab.key));
    button.addEventListener("keydown", (event) => {
      let next;
      if (event.key === "Home") next = tabs[0];
      else if (event.key === "End") next = tabs.at(-1);
      else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        const step = event.key === "ArrowRight" ? 1 : -1;
        next = tabs[(index + step + tabs.length) % tabs.length];
      }
      if (!next) return;
      event.preventDefault();
      showWorkspace(next.key);
      document.getElementById(next.button).focus();
    });
  });
  window.addEventListener("hashchange", syncLocation);
  window.addEventListener("popstate", syncLocation);
  syncLocation();
}
