import { useCallback, useEffect, useRef, useState } from 'react';

const RECONNECT_BASE_DELAY_MS = 1_000;
const RECONNECT_MAX_DELAY_MS = 30_000;
const RECONNECT_MAX_ATTEMPTS = 10;

interface UseWebSocketOptions {
  /** Called once a re-connection (not the initial connection) succeeds. */
  onReconnected?: () => void;
  /** Called on the very first successful connection (not re-connections). */
  onInitialConnect?: () => void;
}

export function useWebSocket(url: string, options: UseWebSocketOptions = {}) {
  const [isConnected, setIsConnected] = useState(false);
  const [isReconnecting, setIsReconnecting] = useState(false);
  // Use a ref-backed queue so drain is atomic (no snapshot-vs-clear race).
  const messageBufferRef = useRef<any[]>([]);
  const [messageCount, setMessageCount] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptsRef = useRef(0);
  /** True once the first successful connection has occurred. */
  const hasConnectedOnceRef = useRef(false);
  /** Prevents auto-reconnect when the component intentionally closes the socket. */
  const intentionalCloseRef = useRef(false);

  const onReconnectedRef = useRef(options.onReconnected);
  useEffect(() => {
    onReconnectedRef.current = options.onReconnected;
  }, [options.onReconnected]);

  const onInitialConnectRef = useRef(options.onInitialConnect);
  useEffect(() => {
    onInitialConnectRef.current = options.onInitialConnect;
  }, [options.onInitialConnect]);

  const scheduleReconnect = useCallback(() => {
    if (reconnectAttemptsRef.current >= RECONNECT_MAX_ATTEMPTS) {
      setIsReconnecting(false);
      return;
    }
    const delay = Math.min(
      RECONNECT_BASE_DELAY_MS * 2 ** reconnectAttemptsRef.current,
      RECONNECT_MAX_DELAY_MS
    );
    reconnectAttemptsRef.current += 1;
    setIsReconnecting(true);
    reconnectTimeoutRef.current = setTimeout(() => {
      connectRef.current?.();
    }, delay);
  }, []);

  // Forward declaration so scheduleReconnect can call connect before it's defined.
  const connectRef = useRef<(() => void) | null>(null);

  const connect = useCallback(() => {
    if (reconnectTimeoutRef.current !== null) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (wsRef.current) {
      // Mark as intentional so onclose doesn't trigger another reconnect.
      intentionalCloseRef.current = true;
      wsRef.current.close();
    }
    // Note: intentionalCloseRef is reset to false in ws.onopen (below) once
    // the replacement socket successfully connects, avoiding the race where
    // the old socket's async onclose fires AFTER we reset the flag here.

    const ws = new WebSocket(url);

    ws.onopen = () => {
      // Reset intentional-close flag now that the new connection is established.
      intentionalCloseRef.current = false;
      setIsConnected(true);
      setIsReconnecting(false);
      reconnectAttemptsRef.current = 0;

      if (hasConnectedOnceRef.current) {
        // This is a re-connection — delay slightly before notifying the caller
        // so the backend WebSocket handler is fully ready to process the
        // resume message (avoids session=none on rapid reconnects).
        setTimeout(() => {
          onReconnectedRef.current?.();
        }, 150);
      } else {
        // First-ever connection — notify the caller so it can resume a
        // session that was active before a hard page refresh.
        onInitialConnectRef.current?.();
      }
      hasConnectedOnceRef.current = true;
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        messageBufferRef.current.push(data);
      } catch {
        messageBufferRef.current.push(event.data);
      }
      setMessageCount((v) => v + 1);
    };

    ws.onclose = () => {
      setIsConnected(false);
      if (!intentionalCloseRef.current) {
        scheduleReconnect();
      } else {
        setIsReconnecting(false);
      }
    };

    ws.onerror = () => {
      // onclose fires after onerror, so reconnect logic lives there.
      setIsConnected(false);
    };

    wsRef.current = ws;
  }, [url, scheduleReconnect]);

  connectRef.current = connect;

  /** Manually trigger an immediate reconnection attempt. */
  const reconnect = useCallback(() => {
    reconnectAttemptsRef.current = 0;
    connect();
  }, [connect]);

  const sendMessage = useCallback((message: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
    }
  }, []);

  /** Atomically returns all queued messages and clears the buffer. */
  const drainMessageQueue = useCallback((): any[] => {
    return messageBufferRef.current.splice(0);
  }, []);

  /** Removes and returns the first queued message, or undefined if empty. */
  const shiftMessage = useCallback((): any | undefined => {
    return messageBufferRef.current.shift();
  }, []);

  useEffect(() => {
    connect();
    return () => {
      intentionalCloseRef.current = true;
      if (reconnectTimeoutRef.current !== null) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      wsRef.current?.close();
    };
  }, [connect]);

  return { isConnected, isReconnecting, drainMessageQueue, sendMessage, reconnect, messageCount, shiftMessage };
}