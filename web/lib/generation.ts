// A counter that tells an answer from before a newer write. A request takes a ticket when it starts; a write
// (useLive's replace: a POST answered with the page) bumps the counter; an answer whose ticket is behind it
// carries data from before the write and must not land over it.
export function generation() {
  let n = 0;
  return {
    ticket: () => n,
    bump: () => { n += 1; },
    fresh: (ticket: number) => ticket === n,
  };
}
