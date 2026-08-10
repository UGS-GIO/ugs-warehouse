// @vitest-environment jsdom
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** A real MediaQueryList: EventTarget supplies the listener plumbing, so dispatching a change
 *  exercises the same path the browser does. Listeners are counted to check we unsubscribe. */
class FakeMediaQueryList extends EventTarget implements MediaQueryList {
  onchange: ((this: MediaQueryList, ev: MediaQueryListEvent) => unknown) | null = null;
  listeners = 0;

  constructor(readonly media: string, public matches: boolean) { super(); }

  addEventListener(...args: Parameters<EventTarget["addEventListener"]>) {
    this.listeners++;
    super.addEventListener(...args);
  }

  removeEventListener(...args: Parameters<EventTarget["removeEventListener"]>) {
    this.listeners--;
    super.removeEventListener(...args);
  }

  resize(to: boolean) {
    this.matches = to;
    this.dispatchEvent(new Event("change"));
  }

  // Deprecated aliases, part of the interface but unused by the hook.
  addListener() {}
  removeListener() {}
}

// The hook reads matchMedia at module load, so each test installs its own before importing.
async function mount(wide: boolean) {
  const mq = new FakeMediaQueryList("(min-width: 768px)", wide);
  window.matchMedia = () => mq;
  vi.resetModules();
  const { useIsDesktop } = await import("./use-breakpoint");
  const Probe = () => <output>{useIsDesktop() ? "desktop" : "phone"}</output>;
  return { mq, view: render(<Probe />) };
}

const shown = () => screen.getByRole("status").textContent;

afterEach(() => { vi.resetModules(); });

describe("useIsDesktop", () => {
  it("reports the width on the first render, with no effect pass", async () => {
    await mount(false);
    // A layout that measured in an effect would paint "desktop" once and then correct itself.
    expect(shown()).toBe("phone");
  });

  it("reports desktop above the md breakpoint", async () => {
    await mount(true);
    expect(shown()).toBe("desktop");
  });

  it("follows a resize across the breakpoint", async () => {
    const { mq } = await mount(false);
    act(() => mq.resize(true));
    expect(shown()).toBe("desktop");
  });

  it("unsubscribes when the last consumer unmounts", async () => {
    const { mq, view } = await mount(true);
    expect(mq.listeners).toBe(1);
    view.unmount();
    expect(mq.listeners).toBe(0);
  });
});
