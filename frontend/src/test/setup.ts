import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// Node 25+ объявляет свой localStorage, пустой без --localstorage-file, и он закрывает хранилище jsdom.
if (typeof globalThis.localStorage === 'undefined') {
  const { jsdom } = globalThis as unknown as { jsdom: { window: Window } };
  Object.defineProperty(globalThis, 'localStorage', { value: jsdom.window.localStorage, configurable: true, writable: true });
}

afterEach(() => {
  cleanup();
});
