"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

type ConnectionState = "idle" | "connecting" | "connected" | "error";

const defaultIceServers = [{ urls: "stun:stun.l.google.com:19302" }];

async function postJSON(url: string, body: unknown) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`);
  }
  return response.json();
}

export function ExperimentalWebRTCPanel() {
  const [state, setState] = useState<ConnectionState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const pcIdRef = useRef<string | null>(null);
  const pendingCandidatesRef = useRef<RTCIceCandidateInit[]>([]);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const reset = useCallback(() => {
    setState("idle");
    setErrorMessage(null);
    pcIdRef.current = null;
  }, []);

  const flushCandidates = useCallback(async () => {
    const candidates = pendingCandidatesRef.current.splice(0);
    if (!candidates.length || !pcIdRef.current) {
      return;
    }
    await postJSON("/api/webrtc-experimental/ice", {
      pcId: pcIdRef.current,
      candidates: candidates.map((candidate) => ({
        candidate: candidate.candidate,
        sdpMid: candidate.sdpMid ?? "",
        sdpMLineIndex: candidate.sdpMLineIndex ?? 0,
      })),
    });
  }, []);

  const stopConnection = useCallback(() => {
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }

    const pc = pcRef.current;
    pcRef.current = null;

    if (pc) {
      pc.getSenders().forEach((sender) => sender.track?.stop());
      pc.close();
    }

    const audio = audioRef.current;
    if (audio && audio.srcObject instanceof MediaStream) {
      audio.srcObject.getTracks().forEach((track) => track.stop());
      audio.srcObject = null;
    }

    reset();
  }, [reset]);

  const startConnection = useCallback(async () => {
    try {
      stopConnection();
      setState("connecting");

      const pc = new RTCPeerConnection({ iceServers: defaultIceServers });
      pcRef.current = pc;

      const remoteStream = new MediaStream();
      if (audioRef.current) {
        audioRef.current.srcObject = remoteStream;
        audioRef.current.autoplay = true;
      }

      pc.ontrack = (event) => {
        event.streams[0]?.getTracks().forEach((track) => remoteStream.addTrack(track));
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

      const localStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      localStream.getTracks().forEach((track) => pc.addTrack(track, localStream));

      const offer = await pc.createOffer({
        offerToReceiveAudio: true,
        offerToReceiveVideo: true,
      });
      await pc.setLocalDescription(offer);

      const answer = await postJSON("/api/webrtc-experimental/offer", {
        sdp: offer.sdp,
        type: offer.type,
      });
      pcIdRef.current = answer.pc_id;
      await pc.setRemoteDescription(answer);

      setState("connected");
    } catch (error) {
      console.error("Failed to start WebRTC session", error);
      setErrorMessage(error instanceof Error ? error.message : "Unknown error");
      setState("error");
    }
  }, [flushCandidates, stopConnection]);

  useEffect(() => {
    return () => {
      stopConnection();
    };
  }, [stopConnection]);

  return (
    <Card className="space-y-4 p-4">
      <CardHeader>
        <CardTitle>Experimental WebRTC Session</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="text-sm text-muted-foreground">
          Status:
          <span className="font-medium text-foreground"> {state}</span>
        </div>
        {errorMessage ? <p className="text-sm text-red-600">{errorMessage}</p> : null}
        <div className="flex gap-2">
          <Button onClick={startConnection} disabled={state === "connecting"}>
            {state === "connected" ? "Reconnect" : "Start Session"}
          </Button>
          <Button variant="secondary" onClick={stopConnection}>
            Stop
          </Button>
        </div>
        <audio ref={audioRef} controls className="w-full" />
      </CardContent>
    </Card>
  );
}

