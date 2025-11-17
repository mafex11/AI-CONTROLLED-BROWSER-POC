"use client";

import { useEffect, useRef, useState, useCallback } from "react";

interface StepData {
  step: number;
  narration: string;
  reasoning?: string;
  tool?: string;
  screenshot?: string;
}

interface VoiceWebSocketProps {
  isEnabled: boolean;
  onUserSpeech?: (text: string) => void;
  onAgentResponse?: (text: string) => void;
  onStep?: (stepData: StepData) => void;
  onError?: (error: string) => void;
  onAudioChunk?: (audio: string, sampleRate: number, numChannels: number) => void;
  onInterruption?: () => void;
}

export function useVoiceWebSocket(props: VoiceWebSocketProps) {
  const { isEnabled, onUserSpeech, onAgentResponse, onStep, onError, onAudioChunk, onInterruption } = props;
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [reconnectTrigger, setReconnectTrigger] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const connectingRef = useRef(false);
  const intentionallyDisconnectedRef = useRef(false);
  
  // Store callbacks in refs to avoid dependency issues
  const callbacksRef = useRef({ onUserSpeech, onAgentResponse, onStep, onError, onAudioChunk, onInterruption });
  useEffect(() => {
    callbacksRef.current = { onUserSpeech, onAgentResponse, onStep, onError, onAudioChunk, onInterruption };
  }, [onUserSpeech, onAgentResponse, onStep, onError, onAudioChunk, onInterruption]);

  useEffect(() => {
    if (!isEnabled) {
      intentionallyDisconnectedRef.current = true;
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      setIsConnected(false);
      setIsConnecting(false);
      connectingRef.current = false;
      return;
    }

    // Don't reconnect if intentionally disconnected (unless it's a triggered reconnect)
    if (intentionallyDisconnectedRef.current && reconnectTrigger === 0) {
      intentionallyDisconnectedRef.current = false;
    }

    // Check if already connected or connecting
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    if (wsRef.current?.readyState === WebSocket.CONNECTING) {
      return;
    }

    if (connectingRef.current) {
      return;
    }

    connectingRef.current = true;
    setIsConnecting(true);

    const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";
    const wsUrl = backendUrl.replace("http://", "ws://").replace("https://", "wss://");
    
    try {
      const ws = new WebSocket(`${wsUrl}/ws/voice`);

      ws.onopen = () => {
        connectingRef.current = false;
        setIsConnected(true);
        setIsConnecting(false);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          const callbacks = callbacksRef.current;
          
          if (data.type === "ready") {
            // Pipeline is ready
            setIsConnected(true);
          } else if (data.type === "user_speech" && callbacks.onUserSpeech) {
            callbacks.onUserSpeech(data.text);
          } else if (data.type === "agent_response" && callbacks.onAgentResponse) {
            callbacks.onAgentResponse(data.text);
          } else if (data.type === "step" && callbacks.onStep) {
            callbacks.onStep({
              step: data.step,
              narration: data.narration || "",
              reasoning: data.reasoning,
              tool: data.tool,
              screenshot: data.screenshot,
            });
          } else if (data.type === "error" && callbacks.onError) {
            callbacks.onError(data.error);
          } else if (data.type === "audio_chunk" && callbacks.onAudioChunk) {
            callbacks.onAudioChunk(data.audio, data.sample_rate, data.num_channels);
          } else if (data.type === "interruption" && callbacks.onInterruption) {
            callbacks.onInterruption();
          }
        } catch (e) {
          console.error("Failed to parse WebSocket message:", e);
        }
      };

      ws.onerror = () => {
        connectingRef.current = false;
        const callbacks = callbacksRef.current;
        if (callbacks.onError) {
          callbacks.onError("WebSocket connection error");
        }
        setIsConnecting(false);
      };

      ws.onclose = (event) => {
        connectingRef.current = false;
        setIsConnected(false);
        setIsConnecting(false);
        wsRef.current = null;
        
        // Only reconnect if:
        // 1. Still enabled
        // 2. Not intentionally disconnected
        // 3. Not a normal closure (code 1000)
        // 4. No reconnect already scheduled
        if (
          isEnabled && 
          !intentionallyDisconnectedRef.current && 
          event.code !== 1000 &&
          !reconnectTimeoutRef.current
        ) {
          reconnectTimeoutRef.current = setTimeout(() => {
            reconnectTimeoutRef.current = null;
            if (isEnabled && !intentionallyDisconnectedRef.current && !wsRef.current) {
              // Trigger reconnection by updating state
              setReconnectTrigger((prev) => prev + 1);
            }
          }, 5000); // Wait 5 seconds before reconnecting
        }
      };

      wsRef.current = ws;
    } catch (error) {
      connectingRef.current = false;
      console.error("Failed to create WebSocket:", error);
      const callbacks = callbacksRef.current;
      if (callbacks.onError) {
        callbacks.onError(error instanceof Error ? error.message : "Failed to connect");
      }
      setIsConnecting(false);
    }

    return () => {
      intentionallyDisconnectedRef.current = true;
      connectingRef.current = false;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setIsConnected(false);
      setIsConnecting(false);
    };
  }, [isEnabled, reconnectTrigger]); // Depend on isEnabled and reconnectTrigger

  const sendText = useCallback((text: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "text_input", text }));
    }
  }, []);
  
  const sendMessage = useCallback((message: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
    }
  }, []);

  const disconnect = useCallback(() => {
    intentionallyDisconnectedRef.current = true;
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (wsRef.current) {
      try {
        if (wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: "stop" }));
        }
      } catch (e) {
        // Ignore errors when sending stop
      }
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsConnected(false);
    setIsConnecting(false);
  }, []);

  return {
    isConnected,
    isConnecting,
    sendText,
    sendMessage,
    disconnect,
  };
}

