import { useEffect, useRef, useState } from "react";

/** Mantém o estado de loading visível por pelo menos `minMs`,
 *  mesmo que a requisição termine antes — evita flash de skeleton. */
export function useDelayedLoading(loading: boolean, minMs = 200): boolean {
  const [show, setShow] = useState(loading);
  const startedAt = useRef<number>(Date.now());

  useEffect(() => {
    if (loading) {
      startedAt.current = Date.now();
      setShow(true);
      return;
    }
    const remaining = Math.max(0, minMs - (Date.now() - startedAt.current));
    const timer = setTimeout(() => setShow(false), remaining);
    return () => clearTimeout(timer);
  }, [loading, minMs]);

  return show || loading;
}
