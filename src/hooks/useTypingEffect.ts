import { useState, useEffect, useRef } from 'react';

/**
 * Smoothly animates text appearing character-by-character toward `targetText`.
 * Continues from the current position when `targetText` grows (streaming),
 * and resets cleanly when `targetText` is cleared.
 *
 * @param targetText Full text to eventually display (may grow over time)
 * @param skip        When true, skips animation and returns targetText immediately
 * @param charsPerSecond Typing speed — default 200 chars/sec
 */
export function useTypingEffect(targetText: string, skip = false, charsPerSecond = 200): string {
  const [displayed, setDisplayed] = useState(skip ? targetText : '');
  const posRef = useRef(skip ? targetText.length : 0);
  const rafRef = useRef<number>(0);
  const lastTimestampRef = useRef<number | null>(null);

  useEffect(() => {
    if (skip) {
      posRef.current = targetText.length;
      setDisplayed(targetText);
      return;
    }

    if (!targetText) {
      posRef.current = 0;
      setDisplayed('');
      return;
    }

    if (posRef.current >= targetText.length) return;

    const msPerChar = 1000 / charsPerSecond;

    const tick = (timestamp: number) => {
      // First frame: calibrate start time, then wait for next frame
      if (lastTimestampRef.current === null) {
        lastTimestampRef.current = timestamp;
        rafRef.current = requestAnimationFrame(tick);
        return;
      }

      const elapsed = timestamp - lastTimestampRef.current;
      const charsToReveal = Math.floor(elapsed / msPerChar);

      if (charsToReveal > 0) {
        posRef.current = Math.min(posRef.current + charsToReveal, targetText.length);
        setDisplayed(targetText.slice(0, posRef.current));
        // Carry over sub-character remainder to keep timing accurate
        lastTimestampRef.current = timestamp - (elapsed % msPerChar);
      }

      if (posRef.current < targetText.length) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };

    rafRef.current = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(rafRef.current);
      // Reset start-time so the next effect run begins timing fresh
      lastTimestampRef.current = null;
    };
  }, [targetText, skip, charsPerSecond]);

  return displayed;
}
