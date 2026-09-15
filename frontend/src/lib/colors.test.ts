import { describe, expect, it } from 'vitest';
import { engineerColor, ENGINEER_PALETTE, UNASSIGNED_COLOR } from './colors';

describe('engineerColor', () => {
  it('assigns colors by engineer position', () => {
    const ids = ['E01', 'E02'];
    expect(engineerColor('E01', ids)).toBe(ENGINEER_PALETTE[0]);
    expect(engineerColor('E02', ids)).toBe(ENGINEER_PALETTE[1]);
  });

  it('wraps around the palette and greys out unknown or missing engineers', () => {
    const ids = Array.from({ length: ENGINEER_PALETTE.length + 1 }, (_, index) => `E${index}`);
    expect(engineerColor(ids[ENGINEER_PALETTE.length], ids)).toBe(ENGINEER_PALETTE[0]);
    expect(engineerColor(null, ids)).toBe(UNASSIGNED_COLOR);
    expect(engineerColor('X', ids)).toBe(UNASSIGNED_COLOR);
  });
});
