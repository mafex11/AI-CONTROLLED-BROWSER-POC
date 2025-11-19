"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "../ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

type ConnectionState = "idle" | "connecting" | "connected" | "error";

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

async function getJSON(url: string) {
  const response = await fetch(url, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Request failed (${response.status})`);
  }
  return response.json();
}

export function BrowserScreenPanel() {
  const [state, setState] = useState<ConnectionState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const pendingCandidatesRef = useRef<RTCIceCandidateInit[]>([]);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const reset = useCallback(() => {
    setState("idle");
    setErrorMessage(null);
    sessionIdRef.current = null;
  }, []);

  const flushCandidates = useCallback(async () => {
    const candidates = pendingCandidatesRef.current.splice(0);
    if (!candidates.length || !sessionIdRef.current) {
      return;
    }
    await postJSON("/api/screen-stream/ice", {
      session_id: sessionIdRef.current,
      candidate: candidates[0],
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
      pc.close();
    }

    const video = videoRef.current;
    if (video && video.srcObject instanceof MediaStream) {
      video.srcObject.getTracks().forEach((track) => track.stop());
      video.srcObject = null;
    }

    reset();
  }, [reset]);

  const startConnection = useCallback(async () => {
    try {
      stopConnection();
      setState("connecting");

      // Fetch ICE servers from backend
      const iceConfig = await getJSON("/api/screen-stream/ice-servers");
      
      const pc = new RTCPeerConnection(iceConfig);
      pcRef.current = pc;

      const remoteStream = new MediaStream();
      if (videoRef.current) {
        videoRef.current.srcObject = remoteStream;
        videoRef.current.autoplay = true;
        videoRef.current.playsInline = true;
      }

      pc.ontrack = (event) => {
        event.streams[0]?.getTracks().forEach((track) => {
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
          flushCandidates().catch(() => {
            // Failed to flush ICE candidates
          });
        }, 300);
      };

      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "connected") {
          setState("connected");
        } else if (pc.connectionState === "failed") {
          setErrorMessage("Connection failed");
          setState("error");
        }
      };

      // Create offer
      const offer = await pc.createOffer({
        offerToReceiveAudio: false,
        offerToReceiveVideo: true,
      });
      await pc.setLocalDescription(offer);

      // Send offer to backend
      const answer = await postJSON("/api/screen-stream/offer", {
        sdp: offer.sdp,
        type: offer.type,
      });
      
      sessionIdRef.current = answer.session_id;
      
      // Set remote description (answer)
      await pc.setRemoteDescription(new RTCSessionDescription({
        sdp: answer.sdp,
        type: answer.type,
      }));
    } catch (error) {
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
        <CardTitle>Browser Screen Stream</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="text-sm text-muted-foreground">
          Status:
          <span className="font-medium text-foreground"> {state}</span>
        </div>
        {errorMessage ? <p className="text-sm text-red-600">{errorMessage}</p> : null}
        <div className="flex gap-2">
          <Button onClick={startConnection} disabled={state === "connecting"}>
            {state === "connected" ? "Reconnect" : "Start Screen Stream"}
          </Button>
          <Button variant="secondary" onClick={stopConnection}>
            Stop
          </Button>
        </div>
        <div className="relative aspect-video w-full overflow-hidden rounded-lg border bg-black">
          <video
            ref={videoRef}
            className="h-full w-full object-contain"
            playsInline
            autoPlay
          />
        </div>
      </CardContent>
    </Card>
  );
}

