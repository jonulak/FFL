import { useId } from "react";

// The possession mark, the way a broadcast scorebug puts a football beside the team with the
// ball: a drawn ball (lucide has none) in currentColor, the laces cut out of it. `key` it on the
// field state so it re-enters (.field-ball) when the unit comes on or crosses the 20.
export function FieldBall({ className = "" }: { className?: string }) {
  const laces = useId();
  return (
    <svg viewBox="0 0 16 10" aria-hidden className={`field-ball shrink-0 ${className}`}>
      <mask id={laces}>
        <rect width="16" height="10" fill="white" />
        <path d="M5.25 5h5.5M6.5 3.9v2.2M8 3.9v2.2M9.5 3.9v2.2" stroke="black" strokeWidth="0.9" strokeLinecap="round" />
      </mask>
      <path d="M0.75 5C2.6 0.9 13.4 0.9 15.25 5C13.4 9.1 2.6 9.1 0.75 5Z" fill="currentColor" mask={`url(#${laces})`} />
    </svg>
  );
}
