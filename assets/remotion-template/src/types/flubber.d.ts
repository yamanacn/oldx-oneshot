declare module 'flubber' {
  export function interpolate(a: string, b: string, o?: {maxSegmentLength?: number; string?: boolean}): (t: number) => string;
  export function toCircle(a: string, x: number, y: number, r: number, o?: {maxSegmentLength?: number}): (t: number) => string;
  export function separate(a: string, b: string[], o?: {maxSegmentLength?: number; single?: boolean}): ((t: number) => string) | ((t: number) => string)[];
}
