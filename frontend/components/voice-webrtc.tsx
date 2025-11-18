"use client";

import { useEffect, useRef, useState, useCallback } from "react";

interface StepData {
  step: number;
  narration: string;
  reasoning?: string;
  tool?: string;
  screenshot?: string;
}

interface VoiceWebRTCProps {
  isEnabled: boolean;
  onUserSpeech?: (text: string) => void;
  onAgentResponse?: (text: string) => void;
  onStep?: (stepData: StepData) => void;
  onError?: (error: string) => void;
  onInterruption?: () => void;
}

type ConnectionState = "idle" | "connecting" | "connected" | "error";

const defaultIceServers = [{ urls: "stun:stun.l.google.com:19302" }];

async function postJSON(url: string, body: unknown) {
  const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";
  const response = await fetch(`${backendUrl}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`);
  }
  return response.json();
}

export function useVoiceWebRTC(props: VoiceWebRTCProps) {
  const { isEnabled, onUserSpeech, onAgentResponse, onStep, onError, onInterruption } = props;
  const [state, setState] = useState<ConnectionState>("idle");
  const [isConnecting, setIsConnecting] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const pcIdRef = useRef<string | null>(null);
  const pendingCandidatesRef = useRef<RTCIceCandidateInit[]>([]);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intentionallyDisconnectedRef = useRef(false);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Store callbacks in refs to avoid dependency issues
  const callbacksRef = useRef({ onUserSpeech, onAgentResponse, onStep, onError, onInterruption });
  useEffect(() => {
    callbacksRef.current = { onUserSpeech, onAgentResponse, onStep, onError, onInterruption };
  }, [onUserSpeech, onAgentResponse, onStep, onError, onInterruption]);

  const flushCandidates = useCallback(async () => {
    const candidates = pendingCandidatesRef.current.splice(0);
    if (!candidates.length || !pcIdRef.current) {
      return;
    }
    try {
      await postJSON("/api/webrtc-experimental/ice", {
        pcId: pcIdRef.current,
        candidates: candidates.map((candidate) => ({
          candidate: candidate.candidate,
          sdpMid: candidate.sdpMid ?? "",
          sdpMLineIndex: candidate.sdpMLineIndex ?? 0,
        })),
      });
    } catch (error) {
      console.error("Failed to flush ICE candidates:", error);
    }
  }, []);

  const stopConnection = useCallback(() => {
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }

    // Close EventSource
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    const pc = pcRef.current;
    pcRef.current = null;

    if (pc) {
      pc.getSenders().forEach((sender) => sender.track?.stop());
      pc.close();
    }

    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }

    const audio = audioRef.current;
    if (audio && audio.srcObject instanceof MediaStream) {
      audio.srcObject.getTracks().forEach((track) => track.stop());
      audio.srcObject = null;
    }

    setState("idle");
    pcIdRef.current = null;
  }, []);

  const startConnection = useCallback(async () => {
    try {
      stopConnection();
      setState("connecting");
      setIsConnecting(true);
      intentionallyDisconnectedRef.current = false;

      const pc = new RTCPeerConnection({ iceServers: defaultIceServers });
      pcRef.current = pc;

      // Create audio element for remote audio
      if (!audioRef.current) {
        audioRef.current = new Audio();
        audioRef.current.autoplay = true;
      }

      const remoteStream = new MediaStream();
      audioRef.current.srcObject = remoteStream;

      pc.ontrack = (event) => {
        console.log("Received remote track:", event.track.kind);
        event.streams[0]?.getTracks().forEach((track) => {
          console.log("Adding track to remote stream:", track.kind);
          remoteStream.addTrack(track);
        });
      };

      pc.onicecandidate = (event) => {
        if (!event.candidate) {
          return;
        }
        pendingCandidatesRef.current.push(event.candidate.toJSON());
        if (flushTimerRef.current) {
          clearTimeout(flushTimerRef.current);
        }
        flushTimerRef.current = setTimeout(() => {
          flushTimerRef.current = null;
          flushCandidates().catch((error) => {
            console.error("Failed to flush ICE candidates", error);
          });
        }, 300);
      };

      pc.oniceconnectionstatechange = () => {
        console.log("ICE connection state:", pc.iceConnectionState);
        if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "closed") {
          const callbacks = callbacksRef.current;
          if (callbacks.onError) {
            callbacks.onError("WebRTC connection failed");
          }
          setState("error");
          setIsConnecting(false);
        }
      };

      // Get user media for microphone
      const localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      mediaStreamRef.current = localStream;
      localStream.getTracks().forEach((track) => {
        console.log("Adding local track:", track.kind);
        pc.addTrack(track, localStream);
      });

      const offer = await pc.createOffer({
        offerToReceiveAudio: true,
        offerToReceiveVideo: false,
      });
      await pc.setLocalDescription(offer);

      const answer = await postJSON("/api/webrtc-experimental/offer", {
        sdp: offer.sdp,
        type: offer.type,
      });
      pcIdRef.current = answer.pc_id;
      await pc.setRemoteDescription(answer);

      // Connect to SSE endpoint for events
      const backendUrl = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";
      // URL encode the pc_id to handle special characters like #
      const encodedPcId = encodeURIComponent(answer.pc_id);
      const eventSource = new EventSource(`${backendUrl}/api/webrtc-experimental/events/${encodedPcId}`);
      eventSourceRef.current = eventSource;

      eventSource.onopen = () => {
        console.log("Event stream connected");
      };

      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          const callbacks = callbacksRef.current;

          if (data.type === "ready") {
            console.log("Event stream ready");
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
          } else if (data.type === "closed") {
            console.log("Event stream closed by server");
            stopConnection();
          }
        } catch (e) {
          console.error("Failed to parse SSE message:", e);
        }
      };

      eventSource.onerror = (error) => {
        console.error("Event stream error:", error);
        const callbacks = callbacksRef.current;
        if (callbacks.onError) {
          callbacks.onError("Event stream connection error");
        }
      };

      setState("connected");
      setIsConnecting(false);
      console.log("WebRTC connection established with event stream");
    } catch (error) {
      console.error("Failed to start WebRTC session", error);
      const callbacks = callbacksRef.current;
      if (callbacks.onError) {
        callbacks.onError(error instanceof Error ? error.message : "Unknown error");
      }
      setState("error");
      setIsConnecting(false);
    }
  }, [flushCandidates, stopConnection]);

  // Handle enable/disable
  useEffect(() => {
    if (isEnabled && state === "idle") {
      startConnection();
    } else if (!isEnabled && state !== "idle") {
      intentionallyDisconnectedRef.current = true;
      stopConnection();
    }
  }, [isEnabled, state, startConnection, stopConnection]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      intentionallyDisconnectedRef.current = true;
      stopConnection();
    };
  }, [stopConnection]);

  const disconnect = useCallback(() => {
    intentionallyDisconnectedRef.current = true;
    stopConnection();
  }, [stopConnection]);

  return {
    isConnected: state === "connected",
    isConnecting,
    sendText: useCallback(() => {
      // Text input not supported in WebRTC mode - voice only
      console.warn("Text input not supported in WebRTC voice mode");
    }, []),
    sendMessage: useCallback(() => {
      // Message sending not supported in WebRTC mode
      console.warn("Message sending not supported in WebRTC voice mode");
    }, []),
    disconnect,
  };
}

